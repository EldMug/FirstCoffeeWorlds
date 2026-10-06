"""Versioned deterministic streams."""

from hashlib import sha256


class Stream:
    def __init__(self, seed, domain):
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError("E_RNG_SEED: seed must be uint64")
        if type(domain) is not str or not domain:
            raise ValueError("E_RNG_DOMAIN: domain must be nonempty text")
        try:
            domain.encode("utf-8")
        except UnicodeError:
            raise ValueError("E_RNG_DOMAIN: domain must be valid UTF-8") from None
        self.seed = seed
        self.domain = domain
        self.counter = 0

    def draw(self):
        payload = f"fcw-dungeon/v1:{self.seed}:{self.domain}:{self.counter}".encode("utf-8")
        self.counter += 1
        return int.from_bytes(sha256(payload).digest()[:8], "big")

    def randbelow(self, n):
        if type(n) is not int or not 1 <= n <= 2**64:
            raise ValueError("E_RNG_BOUND: n must be an integer in 1..2**64")
        limit = 2**64 - (2**64 % n)
        for _ in range(32):
            value = self.draw()
            if value < limit:
                return value % n
        raise ValueError("E_RNG_EXHAUSTED: rejection limit of 32 draws reached")
