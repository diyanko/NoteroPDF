from dataclasses import replace

import pytest

from noteropdf.models import SyncScope, ZoteroItem, ZoteroLibrary


def _item(key, collections=()):
    return ZoteroItem(1, key, None, "", None, collection_keys=collections)


def test_filter_union_intersection_and_direct_membership():
    first = _item("FIRST001", ("COLLECT1", "COLLECT2"))
    second = _item("SECOND02", ("COLLECT2",))
    third = _item("THIRD003", ("CHILD001",))
    unfiled = _item("UNFILED1")
    items = [first, second, third, unfiled]
    assert SyncScope().select(items) == items
    assert SyncScope(collection_keys=("COLLECT1", "COLLECT2")).select(items) == [
        first,
        second,
    ]
    assert SyncScope(item_keys=(third.key, unfiled.key)).select(items) == [
        third,
        unfiled,
    ]
    assert SyncScope(("COLLECT1",), (first.key, second.key)).select(items) == [first]
    assert SyncScope(("COLLECT1",), (third.key,)).select(items) == []
    # Repeated flags never duplicate uploads.
    assert SyncScope(("COLLECT1", "COLLECT1"), (first.key, first.key)).select(
        items
    ) == [first]


@pytest.mark.parametrize(
    "scope",
    [
        SyncScope(item_keys=("FIRST001", "MISSING1")),
        SyncScope(collection_keys=("COLLECT1", "MISSING1")),
    ],
)
def test_missing_selection_never_silently_uses_valid_subset(scope):
    with pytest.raises(ValueError, match="selected"):
        scope.select([_item("FIRST001", ("COLLECT1",))])


def test_same_keys_filter_within_selected_group():
    item = replace(_item("FIRST001", ("COLLECT1",)), library=ZoteroLibrary(123))
    assert SyncScope(("COLLECT1",), ("FIRST001",)).select([item]) == [item]


@pytest.mark.parametrize("key", ["abcD1234", "ABCD123", "ABCD12345", "ABCD/123"])
def test_programmatic_scope_rejects_invalid_keys(key):
    with pytest.raises(ValueError, match="8 uppercase"):
        SyncScope(item_keys=(key,))
    with pytest.raises(ValueError, match="8 uppercase"):
        SyncScope(collection_keys=(key,))
