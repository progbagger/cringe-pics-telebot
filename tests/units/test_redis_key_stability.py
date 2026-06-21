import types

from cringe_pics_telebot.repositories.redis import repo


def test_make_key_is_stable_for_functions_with_same_module_and_qualname() -> None:
    """_make_key should not depend on the function identity (id), only on
    the function module/qualname and the call arguments.
    """

    def _a(x, y=2):
        return x + y

    def _b(x, y=2):
        return x * y

    # Create two distinct function objects but give them the same name so their
    # __qualname__ (and __name__) will be identical. Assign the module to the
    # target module so keys are generated with the same prefix.
    f1 = types.FunctionType(_a.__code__, {}, name="some_fn")
    f2 = types.FunctionType(_b.__code__, {}, name="some_fn")

    mod = "cringe_pics_telebot.repositories.redis.repo"
    f1.__module__ = mod
    f2.__module__ = mod

    # Same args -> same key
    key1 = repo._make_key(f1, 1, y=3)
    key2 = repo._make_key(f2, 1, y=3)
    assert key1 == key2

    # Different args -> different key
    key3 = repo._make_key(f1, 2, y=3)
    assert key1 != key3

    # Repeated call is stable
    assert key1 == repo._make_key(f1, 1, y=3)
