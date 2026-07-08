from src.image_matcher import hamming_similarity


def test_hamming_similarity() -> None:
    assert hamming_similarity("ffffffffffffffff", "ffffffffffffffff") == 100
    assert hamming_similarity("0000000000000000", "ffffffffffffffff") == 0

