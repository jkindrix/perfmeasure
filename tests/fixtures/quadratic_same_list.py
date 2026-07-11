def find_dupes(items):
    dupes = []
    for a in items:
        for b in items:
            if a is not b and a == b:
                dupes.append(a)
    return dupes
