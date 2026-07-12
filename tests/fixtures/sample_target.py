def linear(xs: list[int]) -> int:
    acc = 0
    for x in xs:
        acc += x
    return acc


def rejects_everything(xs: list[int]) -> int:
    raise ValueError("nope")


def untyped(xs):
    return xs


def prints_to_stdout(xs: list[int]) -> int:
    print("target noise on stdout")
    return len(xs)


def slow_sleeper(xs: list[int]) -> int:
    import time
    time.sleep(0.3)
    return len(xs)


def sorts_in_place(xs: list[int]) -> None:
    xs.sort()
