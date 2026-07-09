import pytest
from pathlib import Path
from src.config import AppConfig
from src.parsers.generic import GenericParser
from src.parsers.base import ParserOutput
from src.visual_price import VisualPriceResult
import src.parsers.generic

@pytest.fixture
def mock_config():
    config = AppConfig()
    config.enable_screenshot = True
    config.enable_ocr = True
    config.headless = True
    return config

def test_generic_parser_visual_fallback(mock_config, monkeypatch):
    parser = GenericParser(mock_config)
    
    # Mock network to return a dummy page with no price
    monkeypatch.setattr(parser, "fetch_page", lambda url: "<html><body>No price here</body></html>")
    
    # Mock capture_screenshot
    def mock_capture(*args, **kwargs):
        return str(args[1]), "ok"
    monkeypatch.setattr(src.parsers.generic, "capture_screenshot", mock_capture)
    
    # Mock VisualPriceExtractor
    class MockExtractor:
        def extract_from_screenshot(self, screenshot_path, platform=None):
            return VisualPriceResult(
                price=2480.0,
                raw_text="售價 $2,480",
                confidence=0.8,
                method="mock_ocr",
                screenshot_path=screenshot_path,
                crop_path=None,
                error_message=None
            )
            
    monkeypatch.setattr(src.parsers.generic, "VisualPriceExtractor", MockExtractor)
    
    # Run parse
    output = parser.parse("https://example.com/item", Path("output"))
    
    # Assert
    assert output.price == 2480.0
    assert output.parse_status == "ok"
    assert output.raw_data.get("price_source") == "visual_ocr"
    assert output.raw_data.get("visual_price_used") is True
    assert output.raw_data.get("visual_price_method") == "mock_ocr"
    assert output.raw_data.get("visual_price_confidence") == 0.8
    assert "2480.0" in output.evidence_text

def test_generic_parser_visual_fallback_failure(mock_config, monkeypatch):
    parser = GenericParser(mock_config)
    
    monkeypatch.setattr(parser, "fetch_page", lambda url: "<html><body>No price here</body></html>")
    
    def mock_capture(*args, **kwargs):
        return str(args[1]), "ok"
    monkeypatch.setattr(src.parsers.generic, "capture_screenshot", mock_capture)
    
    # Mock VisualPriceExtractor to fail
    class MockExtractor:
        def extract_from_screenshot(self, screenshot_path, platform=None):
            return VisualPriceResult(
                price=None,
                raw_text="雜訊",
                confidence=0.0,
                method="mock_ocr",
                screenshot_path=screenshot_path,
                crop_path=None,
                error_message="Could not read price"
            )
            
    monkeypatch.setattr(src.parsers.generic, "VisualPriceExtractor", MockExtractor)
    
    # Run parse
    output = parser.parse("https://example.com/item", Path("output"))
    
    # Assert
    assert output.price is None
    assert output.parse_status == "price_not_found"
    assert output.raw_data.get("price_source") == "unknown"
    assert "Error" in output.evidence_text
