//! Ground-truth corpus (Rust): functions with known time/space complexity.

use std::collections::HashMap;

// --- O(1) ----------------------------------------------------------------

pub fn rs_head(xs: &[i64]) -> i64 {
    if xs.is_empty() { 0 } else { xs[0] }
}

// --- O(log n) --------------------------------------------------------------

pub fn rs_binary_search(xs: &[i64], target: i64) -> usize {
    let (mut lo, mut hi) = (0usize, xs.len());
    while lo < hi {
        let mid = (lo + hi) / 2;
        if xs[mid] < target { lo = mid + 1 } else { hi = mid }
    }
    lo
}

pub fn rs_halving(n: u64) -> u64 {
    let mut n = n;
    let mut steps = 0;
    while n > 1 {
        n /= 2;
        steps += 1;
    }
    steps
}

// --- O(n) ------------------------------------------------------------------

pub fn rs_sum(xs: &[i64]) -> i64 {
    let mut acc: i64 = 0;
    for x in xs {
        acc = acc.wrapping_add(*x);
    }
    acc
}

pub fn rs_copy(xs: &[i64]) -> Vec<i64> {
    xs.to_vec()
}

pub fn rs_concat(words: &[String]) -> String {
    let mut s = String::new();
    for w in words {
        s.push_str(w);
    }
    s
}

pub fn rs_count_char(s: &str) -> usize {
    s.chars().filter(|c| *c == 'a').count()
}

pub fn rs_count_zero_bytes(data: &[u8]) -> usize {
    data.iter().filter(|b| **b == 0).count()
}

pub fn rs_map_lookup_all(m: &HashMap<i64, i64>, keys: &[i64]) -> i64 {
    let mut acc = 0;
    for k in keys {
        acc += m.get(k).copied().unwrap_or(0);
    }
    acc
}

// --- O(n log n) ---------------------------------------------------------------

pub fn rs_sort_copy(xs: &[i64]) -> Vec<i64> {
    let mut v = xs.to_vec();
    v.sort();
    v
}

pub fn rs_sort_unstable_copy(xs: &[i64]) -> Vec<i64> {
    let mut v = xs.to_vec();
    v.sort_unstable();
    v
}

// --- O(n^2) ----------------------------------------------------------------------

pub fn rs_pair_count(xs: &[i64]) -> i64 {
    let mut count = 0;
    for a in xs {
        for b in xs {
            if (a.wrapping_add(*b)) % 7 == 0 {
                count += 1;
            }
        }
    }
    count
}

pub fn rs_dedup_quadratic(xs: &[i64]) -> Vec<i64> {
    let mut out: Vec<i64> = Vec::new();
    for x in xs {
        if !out.contains(x) {
            out.push(*x);
        }
    }
    out
}

pub fn rs_insertion_sort(xs: &[i64]) -> Vec<i64> {
    let mut out = xs.to_vec();
    for i in 1..out.len() {
        let key = out[i];
        let mut j = i;
        while j > 0 && out[j - 1] > key {
            out[j] = out[j - 1];
            j -= 1;
        }
        out[j] = key;
    }
    out
}

// --- O(n^3) ---------------------------------------------------------------------

pub fn rs_triple_count(xs: &[i64]) -> i64 {
    let mut count = 0;
    for a in xs {
        for b in xs {
            for c in xs {
                if (a.wrapping_add(*b).wrapping_add(*c)) % 7 == 0 {
                    count += 1;
                }
            }
        }
    }
    count
}

// --- O(2^n) ---------------------------------------------------------------------

pub fn rs_fib(n: u64) -> u64 {
    if n < 2 { n } else { rs_fib(n - 1).wrapping_add(rs_fib(n - 2)) }
}

// --- constructible receivers and instance params -----------------------------------

#[derive(Default)]
pub struct RsScaler {
    factor: i64,
}

impl RsScaler {
    pub fn rs_scale(&self, xs: &[i64]) -> Vec<i64> {
        xs.iter().map(|x| x.wrapping_mul(self.factor + 2)).collect()
    }

    pub fn rs_pair_count(&self, xs: &[i64]) -> i64 {
        let mut count = 0;
        for a in xs {
            for b in xs {
                if (a.wrapping_add(*b)) % 7 == self.factor % 7 {
                    count += 1;
                }
            }
        }
        count
    }
}

pub struct RsOpts {
    pub strict: bool,
}

impl RsOpts {
    pub fn new() -> Self {
        RsOpts { strict: false }
    }
}

pub struct RsIndex {
    modulus: i64,
}

impl RsIndex {
    pub fn new(modulus: i64, label: String) -> Self {
        let _ = label;
        RsIndex { modulus: modulus + 1 }
    }

    pub fn rs_count_multiples(&self, xs: &[i64]) -> usize {
        xs.iter().filter(|x| **x % self.modulus == 0).count()
    }
}

pub fn rs_apply_opts(xs: &[i64], opts: &RsOpts) -> i64 {
    if opts.strict {
        xs.len() as i64
    } else {
        xs.iter().filter(|x| **x > 0).count() as i64
    }
}

// --- honest failures --------------------------------------------------------------

pub fn rs_always_panics(xs: &[i64]) -> i64 {
    xs[usize::MAX - 1]
}

type Ids = Vec<i64>;

pub fn rs_takes_alias(xs: Ids) -> usize {
    xs.len()
}

pub fn rs_generic<T: Ord>(xs: Vec<T>) -> Vec<T> {
    let mut v = xs;
    v.sort();
    v
}

#[cfg(feature = "never-enabled")]
pub fn rs_feature_gated(xs: &[i64]) -> i64 {
    xs.len() as i64
}
