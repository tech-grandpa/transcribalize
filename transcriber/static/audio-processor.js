/**
 * AudioWorklet processor for capturing and encoding audio to PCM16.
 * 
 * Captures audio at 16kHz mono and sends chunks every 100ms.
 */
class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        
        // Target 100ms chunks at 16kHz = 1600 samples
        this.bufferSize = 1600;
        this.buffer = new Int16Array(this.bufferSize);
        this.bufferIndex = 0;
        
        // Handle messages from main thread
        this.port.onmessage = (event) => {
            if (event.data.type === 'flush') {
                this.flush();
            }
        };
    }
    
    /**
     * Convert float32 sample [-1, 1] to int16 [-32768, 32767]
     */
    floatToInt16(sample) {
        // Clamp to [-1, 1]
        const clamped = Math.max(-1, Math.min(1, sample));
        // Scale to int16 range
        return Math.round(clamped * 32767);
    }
    
    /**
     * Send current buffer to main thread and reset.
     */
    flush() {
        if (this.bufferIndex > 0) {
            // Send only the filled portion
            const data = this.buffer.slice(0, this.bufferIndex);
            this.port.postMessage({
                type: 'audio',
                samples: data
            }, [data.buffer]);
            
            // Reset buffer
            this.buffer = new Int16Array(this.bufferSize);
            this.bufferIndex = 0;
        }
    }
    
    /**
     * Process audio frames.
     * @param {Float32Array[][]} inputs - Input audio channels
     * @returns {boolean} - Keep processor alive
     */
    process(inputs) {
        const input = inputs[0];
        
        // No input or no channels
        if (!input || !input.length || !input[0]) {
            return true;
        }
        
        // Use first channel (mono)
        const samples = input[0];
        
        for (let i = 0; i < samples.length; i++) {
            this.buffer[this.bufferIndex++] = this.floatToInt16(samples[i]);
            
            // Buffer full, send it
            if (this.bufferIndex >= this.bufferSize) {
                this.flush();
            }
        }
        
        return true;
    }
}

registerProcessor('pcm-processor', PCMProcessor);
