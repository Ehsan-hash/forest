from services.api.archive_paths import case_collision


def test_case_colliding_names_are_reported():
    assert case_collision(["files/Readme.md", "files/README.md"]) == (
        "files/Readme.md",
        "files/README.md",
    )


def test_identical_and_distinct_names_are_not_collisions():
    assert case_collision(["files/a.txt", "files/a.txt"]) is None
    assert case_collision(["files/a.txt", "files/b.txt"]) is None
