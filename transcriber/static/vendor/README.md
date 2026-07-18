# Vendored browser dependencies

These browser bundles are committed locally so the self-hosted UI does not make
runtime requests to third-party CDNs.

- **Marked 18.0.6** — MIT; see `marked.LICENSE`.
- **DOMPurify 3.4.12** — Apache-2.0 or MPL-2.0; see
  `dompurify.LICENSE` and `dompurify.LICENSE-MPL`.

`index.html` renders Markdown with Marked and sanitizes the generated HTML with
DOMPurify before inserting it into the document. Keep both packages pinned and
run their package-manager vulnerability audit before updating these files.
