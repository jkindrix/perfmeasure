"""M-class corpus: methods on zero-arg-constructible classes and
constructible instance params (the constructible-receiver class)."""


class Accumulator:
    def __init__(self):
        self.factor = 3

    def scale(self, xs: list[int]) -> list[int]:
        return [x * self.factor for x in xs]

    def pair_count(self, xs: list[int]) -> int:
        count = 0
        for a in xs:
            for b in xs:
                if (a + b) % 7 == self.factor % 7:
                    count += 1
        return count


class NeedsArgs:
    def __init__(self, path: str):
        self.path = path

    def work(self, xs: list[int]) -> int:
        return len(xs)


class Options:
    def __init__(self, strict: bool = False):
        self.strict = strict


def apply_options(xs: list[int], opts: Options) -> list[int]:
    if opts.strict:
        return [x for x in xs if x > 0]
    return [x * 2 for x in xs]
