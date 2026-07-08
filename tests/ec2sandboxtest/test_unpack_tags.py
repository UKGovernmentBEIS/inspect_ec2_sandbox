import re

import pytest

from ec2sandbox._unpack_tags import convert_tags_for_aws_interface, unpack_tags


def test_parses_multiple_tags() -> None:
    """Pin the documented 'key1=value1;key2=value2' delimiter contract."""
    assert unpack_tags("team=research;env=dev") == (
        ("team", "research"),
        ("env", "dev"),
    )


def test_missing_equals_raises_value_error() -> None:
    """A segment with no '=' is rejected, not passed through mangled."""
    with pytest.raises(ValueError):
        unpack_tags("noequals")


def test_equals_in_value_raises_value_error() -> None:
    """A value containing '=' is rejected: parsing is a strict single split."""
    # Pins current behaviour deliberately: switching to split-on-first-'='
    # (allowing '=' in values) would be a behaviour change and should
    # surface here.
    with pytest.raises(ValueError):
        unpack_tags("key=a=b")


def test_empty_and_none_return_empty_tuple() -> None:
    """No extra tags configured (the default case) yields an empty tuple."""
    assert unpack_tags(None) == ()
    assert unpack_tags("") == ()


def test_error_message_contains_offending_input() -> None:
    """The parse error names the input that failed to parse."""
    with pytest.raises(ValueError, match=re.escape("bad-tag-input")):
        unpack_tags("bad-tag-input")


def test_convert_tags_shape_for_aws() -> None:
    """Tags convert to the TagSpecifications shape cleanup relies on."""
    result = convert_tags_for_aws_interface("instance", (("k1", "v1"), ("k2", "v2")))
    assert result == [
        {
            "ResourceType": "instance",
            "Tags": [
                {"Key": "k1", "Value": "v1"},
                {"Key": "k2", "Value": "v2"},
            ],
        }
    ]
