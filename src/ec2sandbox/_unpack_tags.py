from typing import Tuple


def unpack_tags(tags: str | None) -> Tuple[Tuple[str, str], ...]:
    tags_unpacked: list[Tuple[str, str]] = []
    if tags:
        try:
            tags_list = tags.split(";")
            for tag in tags_list:
                key, value = tag.split("=")
                tags_unpacked.append((key, value))
        except ValueError:
            raise ValueError(
                "Tags must be in the format 'key1=value1;key2=value2', "
                "but instead got {tags}"
            )
    return tuple(tags_unpacked)


def convert_tags_for_aws_interface(
    resource_type,
    tags_unpacked: Tuple[Tuple[str, str], ...],
) -> list:
    return [
        {
            "ResourceType": resource_type,
            "Tags": [{"Key": k, "Value": v} for k, v in tags_unpacked],
        }
    ]
