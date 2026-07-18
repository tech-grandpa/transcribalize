"""Voice Activity Detection using Silero VAD."""

from collections import deque

import numpy as np
import torch

# Singleton VAD model
_vad_model = None
_vad_utils = None


def get_vad_model():
    """Get or initialize the Silero VAD model (singleton)."""
    global _vad_model, _vad_utils
    
    if _vad_model is None:
        try:
            print("Loading Silero VAD model...")
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False,
            )
            _vad_model = model
            _vad_utils = utils
            print("Silero VAD model loaded")
        except Exception as e:
            print(f"Failed to load Silero VAD: {e}")
            raise RuntimeError(
                "VAD model initialization failed. "
                "Ensure the model is pre-downloaded or network is available."
            ) from e
    
    return _vad_model, _vad_utils


class SileroVAD:
    """
    Voice Activity Detector using Silero VAD.
    
    Designed for real-time streaming: processes audio in chunks and
    detects speech boundaries for optimal transcription chunking.
    """
    
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 500,
        speech_pad_ms: int = 100,
    ):
        """
        Initialize VAD.
        
        Args:
            sample_rate: Audio sample rate (must be 16000 for Silero)
            threshold: Speech probability threshold (0-1)
            min_speech_duration_ms: Minimum speech segment duration
            min_silence_duration_ms: Silence duration to end speech segment
            speech_pad_ms: Padding around speech segments
        """
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        
        # Get model
        self.model, self.utils = get_vad_model()
        
        # State for streaming
        self.reset()
    
    def reset(self):
        """Reset VAD state for a new session."""
        self.model.reset_states()
        self.is_speaking = False
        self.speech_start_sample = 0
        self.silence_samples = 0
        self.current_speech_samples = 0
    
    def _ms_to_samples(self, ms: int) -> int:
        """Convert milliseconds to samples."""
        return int(ms * self.sample_rate / 1000)
    
    def process_chunk(self, audio: np.ndarray) -> dict:
        """
        Process an audio chunk and detect speech activity.
        
        Args:
            audio: Float32 audio samples [-1, 1]
            
        Returns:
            Dict with:
                - is_speech: bool, whether chunk contains speech
                - speech_prob: float, speech probability
                - should_segment: bool, whether to end current segment
                - segment_end_sample: int, where to cut if segmenting
        """
        # Convert to tensor
        audio_tensor = torch.from_numpy(audio).float()
        
        # Get speech probability
        # Silero expects 512 samples at a time for 16kHz
        chunk_size = 512
        probs = []
        
        for i in range(0, len(audio_tensor), chunk_size):
            chunk = audio_tensor[i:i + chunk_size]
            if len(chunk) < chunk_size:
                # Pad with zeros
                chunk = torch.nn.functional.pad(chunk, (0, chunk_size - len(chunk)))
            
            prob = self.model(chunk, self.sample_rate).item()
            probs.append(prob)
        
        avg_prob = sum(probs) / len(probs) if probs else 0.0
        is_speech = avg_prob >= self.threshold
        
        result = {
            "is_speech": is_speech,
            "speech_prob": avg_prob,
            "should_segment": False,
            "segment_end_sample": 0,
        }
        
        chunk_samples = len(audio)
        
        if is_speech:
            self.silence_samples = 0
            
            if not self.is_speaking:
                # Speech started
                self.is_speaking = True
                self.speech_start_sample = 0
                self.current_speech_samples = chunk_samples
            else:
                # Speech continuing
                self.current_speech_samples += chunk_samples
        else:
            if self.is_speaking:
                # Silence during speech
                self.silence_samples += chunk_samples
                
                min_silence = self._ms_to_samples(self.min_silence_duration_ms)
                min_speech = self._ms_to_samples(self.min_speech_duration_ms)
                
                if self.silence_samples >= min_silence and self.current_speech_samples >= min_speech:
                    # End of speech segment
                    result["should_segment"] = True
                    result["segment_end_sample"] = self.current_speech_samples
                    
                    # Reset for next segment
                    self.is_speaking = False
                    self.current_speech_samples = 0
                    self.silence_samples = 0
        
        return result


class SmartChunker:
    """
    Smart audio chunking based on VAD for optimal transcription.
    
    Accumulates audio and chunks on speech boundaries rather than
    fixed time intervals, improving transcription accuracy.
    """
    
    def __init__(
        self,
        sample_rate: int = 16000,
        min_chunk_duration: float = 1.0,
        max_chunk_duration: float = 10.0,
        vad_threshold: float = 0.5,
    ):
        """
        Initialize chunker.
        
        Args:
            sample_rate: Audio sample rate
            min_chunk_duration: Minimum chunk duration in seconds
            max_chunk_duration: Maximum chunk duration in seconds
            vad_threshold: VAD speech probability threshold
        """
        self.sample_rate = sample_rate
        self.min_chunk_samples = int(min_chunk_duration * sample_rate)
        self.max_chunk_samples = int(max_chunk_duration * sample_rate)
        
        # VAD for speech boundary detection
        self.vad = SileroVAD(
            sample_rate=sample_rate,
            threshold=vad_threshold,
        )
        
        # Use deque instead of concatenating arrays
        self._audio_chunks: deque[np.ndarray] = deque()
        self._total_samples: int = 0
    
    def reset(self):
        """Reset chunker state."""
        self._audio_chunks.clear()
        self._total_samples = 0
        self.vad.reset()
    
    def _get_buffer(self) -> np.ndarray:
        """Materialize buffer only when needed."""
        if not self._audio_chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(list(self._audio_chunks))
    
    def _clear_buffer(self):
        """Clear the buffer after extracting a chunk."""
        self._audio_chunks.clear()
        self._total_samples = 0
    
    def add_audio(self, audio: np.ndarray) -> list[np.ndarray]:
        """
        Add audio and return any complete chunks.
        
        Args:
            audio: Float32 audio samples [-1, 1]
            
        Returns:
            List of audio chunks ready for transcription
        """
        self._audio_chunks.append(audio)
        self._total_samples += len(audio)
        
        chunks = []
        
        # Process VAD on new audio
        vad_result = self.vad.process_chunk(audio)
        
        # Check if we should create a chunk
        should_chunk = False
        
        if vad_result["should_segment"] and self._total_samples >= self.min_chunk_samples:
            # VAD detected end of speech
            should_chunk = True
        elif self._total_samples >= self.max_chunk_samples:
            # Max duration reached
            should_chunk = True
        
        if should_chunk:
            chunk = self._get_buffer()
            self._clear_buffer()
            chunks.append(chunk)
        
        return chunks
    
    def flush(self) -> np.ndarray | None:
        """
        Get any remaining audio in buffer.
        
        Returns:
            Remaining audio or None if buffer is too short
        """
        if self._total_samples >= self.min_chunk_samples:
            chunk = self._get_buffer()
            self._clear_buffer()
            return chunk
        return None
