from src.matcher import classify_match, match_score, normalize_name


def test_normalize_name() -> None:
    assert normalize_name("AFC_綠藻錠狀食品(袋裝)") == "綠藻"


def test_match_score_related_product() -> None:
    score = match_score("AFC胺基酸", "AFC 胺基酸 官方測試商品")
    assert score >= 85
    assert classify_match(score) == "matched"
