"""Select a deterministic, 1-based shard of the pytest collection."""

import sys


def pytest_addoption(parser):
    parser.addoption("--shard-index", type=int, help="1-based test shard to run")
    parser.addoption("--shard-count", type=int, help="Total number of test shards")


def pytest_cmdline_main(config):
    index = config.getoption("--shard-index")
    count = config.getoption("--shard-count")
    if index is None and count is None:
        return
    if index is None or count is None or not 1 <= index <= count:
        print(
            "ERROR: use both shard options with 1 <= --shard-index <= --shard-count",
            file=sys.stderr,
        )
        return 4  # pytest usage error


def pytest_collection_modifyitems(config, items):
    count = config.getoption("--shard-count")
    if count is None:
        return
    index = config.getoption("--shard-index")

    selected = []
    deselected = []
    for position, item in enumerate(sorted(items, key=lambda item: item.nodeid)):
        if position % count == index - 1:
            selected.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
