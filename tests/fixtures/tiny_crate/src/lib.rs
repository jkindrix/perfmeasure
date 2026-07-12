pub fn head(xs: &[i64]) -> i64 {
    if xs.is_empty() { 0 } else { xs[0] }
}

pub fn sum_slice(xs: &[i64]) -> i64 {
    let mut acc: i64 = 0;
    for x in xs {
        acc = acc.wrapping_add(*x);
    }
    acc
}

pub fn sort_copy(xs: &[i64]) -> Vec<i64> {
    let mut v = xs.to_vec();
    v.sort();
    v
}

pub fn pair_count(xs: &[i64]) -> i64 {
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

pub fn concat_all(words: &[String]) -> String {
    let mut s = String::new();
    for w in words {
        s.push_str(w);
    }
    s
}

pub fn fib(n: u64) -> u64 {
    if n < 2 { n } else { fib(n - 1).wrapping_add(fib(n - 2)) }
}

pub fn always_panics(xs: &[i64]) -> i64 {
    xs[usize::MAX - 1]
}

pub fn takes_generic<T: Clone>(x: T) -> T {
    x.clone()
}

pub fn takes_mut(xs: &mut Vec<i64>) {
    xs.push(1);
}

pub fn count_zero_bytes(data: &[u8]) -> usize {
    data.iter().filter(|b| **b == 0).count()
}

pub fn sum_if(xs: &[i64], verbose: bool) -> i64 {
    let mut acc: i64 = 0;
    for x in xs {
        acc = acc.wrapping_add(*x);
        if verbose {
            acc = acc.wrapping_add(1);
        }
    }
    acc
}

pub fn join_parts(parts: &[&str]) -> String {
    parts.concat()
}

pub fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() { 0.0 } else { xs.iter().sum::<f64>() / xs.len() as f64 }
}

pub fn capped_sum(xs: &[i64], _timeout: std::time::Duration) -> i64 {
    xs.iter().sum()
}

#[cfg(windows)]
pub fn windows_only(xs: &[i64]) -> i64 {
    xs.len() as i64
}

pub fn opt_label(xs: &[i64], label: Option<&str>) -> usize {
    xs.len() + label.map_or(0, str::len)
}

pub fn sum_u64(values: &[u64]) -> u64 {
    values.iter().sum()
}

pub fn find_byte(data: &[u8], needle: u8) -> usize {
    data.iter().position(|b| *b == needle).unwrap_or(data.len())
}

pub fn takes_path(path: &std::path::Path) -> bool {
    path.exists()
}

pub async fn fetch_all(xs: &[i64]) -> i64 {
    xs.iter().sum()
}

pub struct Codec;

impl Codec {
    pub fn assoc_sum(xs: &[i64]) -> i64 {
        xs.iter().sum()
    }

    pub fn with_receiver(&self, xs: &[i64]) -> i64 {
        xs.len() as i64
    }

    fn private_helper(xs: &[i64]) -> i64 {
        xs.len() as i64
    }
}

mod private_mod {
    pub fn hidden(xs: &[i64]) -> i64 {
        xs.len() as i64
    }
}

#[cfg(test)]
mod tests {
    pub fn test_helper(xs: &[i64]) -> i64 {
        xs.len() as i64
    }
}

pub fn uses_private(xs: &[i64]) -> i64 {
    private_mod::hidden(xs)
}
