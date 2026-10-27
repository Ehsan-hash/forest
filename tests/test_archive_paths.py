from services.api.archive_paths import case_collision
import pytest


def test_case_colliding_names_are_reported():
    assert case_collision(["files/Readme.md", "files/README.md"]) == (
        "files/Readme.md",
        "files/README.md",
    )


@pytest.mark.parametrize(
    ('names', 'expected'),
    [
        (("files/a.txt", "files/a.txt"), ("files/a.txt", "files/a.txt")),
        (("files/a/./result.txt", "files/a/result.txt"), ("files/a/./result.txt", "files/a/result.txt")),
        (("files/café.txt", "files/cafe\u0301.txt"), ("files/café.txt", "files/cafe\u0301.txt")),
    ],
)
def test_destination_aliases_are_reported(names, expected):
    assert case_collision(names) == expected


def test_distinct_destinations_are_not_collisions():
    assert case_collision(["files/a.txt", "files/b.txt", "files/cafe.txt"]) is None
