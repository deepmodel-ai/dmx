from pyutils import cents


def test_cents() -> None:
    assert cents(12.34) == 1234
