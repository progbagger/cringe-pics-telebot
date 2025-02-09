import pytest


@pytest.fixture(autouse=True)
def autocreate_tables(create_tables):
    pass
