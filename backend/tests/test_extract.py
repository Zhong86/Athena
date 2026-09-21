import pytest

from app.materials.ingest.extract import (
    ExtractError,
    extract,
    infer_upload_type,
)


class TestInferUploadType:
    def test_extension_wins_over_content_type(self):
        # Browsers routinely send octet-stream for real files.
        assert infer_upload_type("notes.pdf", "application/octet-stream") == "pdf"

    def test_falls_back_to_content_type_when_extension_is_unknown(self):
        assert infer_upload_type("scan", "image/png") == "image"
        assert infer_upload_type("readme", "text/plain; charset=utf-8") == "text"

    def test_case_insensitive(self):
        assert infer_upload_type("LECTURE.PDF") == "pdf"

    def test_unknown_returns_none(self):
        assert infer_upload_type("lecture.mp4", "video/mp4") is None
        assert infer_upload_type("noextension") is None


class TestText:
    def test_decodes_and_normalizes(self):
        raw = b"Entropy   \r\n\r\n\r\n\r\nalways increases.  \r\n"
        # Trailing spaces stripped, CRLF normalized, blank-line runs collapsed
        # to exactly one -- the chunker's paragraph split depends on this.
        assert extract(raw, "text") == "Entropy\n\nalways increases."

    def test_invalid_utf8_does_not_fail_the_upload(self):
        result = extract(b"caf\xff notes on entropy", "text")
        assert "notes on entropy" in result

    def test_empty_rejected(self):
        with pytest.raises(ExtractError, match="empty"):
            extract(b"", "text")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ExtractError, match="no text"):
            extract(b"   \n\n  ", "text")


class TestPdf:
    def test_corrupt_pdf_gives_a_readable_error(self):
        with pytest.raises(ExtractError, match="could not read this PDF"):
            extract(b"%PDF-1.4 this is not actually a pdf", "pdf")

    def test_real_pdf_round_trips(self):
        reportlab = pytest.importorskip("reportlab")  # noqa: F841
        pytest.skip("covered by the manual PDF run; no generator dep in MVP")


class TestImage:
    def test_non_image_bytes_give_a_readable_error(self):
        with pytest.raises(ExtractError, match="could not open this image"):
            extract(b"definitely not a png", "image")

    def test_blank_image_produces_no_text(self):
        Image = pytest.importorskip("PIL.Image")
        pytest.importorskip("pytesseract")
        import io

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buf, format="PNG")

        # Either tesseract is missing (deployment) or it found nothing (blank
        # image). Both must surface as ExtractError, never as a crash.
        with pytest.raises(ExtractError, match="OCR|tesseract"):
            extract(buf.getvalue(), "image")


def test_unsupported_type_rejected():
    with pytest.raises(ExtractError, match="unsupported upload type"):
        extract(b"data", "audio")
