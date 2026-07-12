// perfmeasure Rust harness — GENERATED, do not edit.
// Speaks the perfmeasure JSON-lines protocol on a private dup of stdout;
// fd 1 is rebound to stderr so target println!() cannot corrupt it.
#![allow(clippy::all, unused_variables, unused_mut, dead_code)]
use std::alloc::{GlobalAlloc, Layout, System};
use std::hint::black_box;
use std::io::{BufRead, Write};
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use serde::Deserialize;
use serde_json::{json, Value};

// ---- counting allocator: true heap peak, everything Rust allocates ----
struct Counting;
static CURRENT: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        let p = System.alloc(l);
        if !p.is_null() {
            let cur = CURRENT.fetch_add(l.size(), Ordering::Relaxed) + l.size();
            PEAK.fetch_max(cur, Ordering::Relaxed);
        }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        CURRENT.fetch_sub(l.size(), Ordering::Relaxed);
        System.dealloc(p, l);
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, new: usize) -> *mut u8 {
        let q = System.realloc(p, l, new);
        if !q.is_null() {
            if new >= l.size() {
                let cur = CURRENT.fetch_add(new - l.size(), Ordering::Relaxed)
                    + (new - l.size());
                PEAK.fetch_max(cur, Ordering::Relaxed);
            } else {
                CURRENT.fetch_sub(l.size() - new, Ordering::Relaxed);
            }
        }
        q
    }
}
#[global_allocator]
static ALLOC: Counting = Counting;

fn reset_peak() -> usize {
    let cur = CURRENT.load(Ordering::Relaxed);
    PEAK.store(cur, Ordering::Relaxed);
    cur
}

// ---- deterministic rng (SplitMix64) — streams need not match Python's ----
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }
}

#[derive(Deserialize, Clone)]
struct Spec {
    spec_type: String,
    shape: String,
    size: u64,
    seed: u64,
    #[serde(default)]
    of_index: Option<usize>,
}

#[derive(Deserialize)]
struct CallReq {
    id: String,
    fid: String,
    inputs: Vec<Spec>,
    #[serde(default = "one")]
    warmup: u32,
    #[serde(default = "fifteen")]
    max_repeats: u32,
    #[serde(default = "ten")]
    min_total_ms: u64,
    #[serde(default)]
    measure: Vec<String>,
    #[serde(default = "ten_thousand")]
    budget_ms: u64,
}
fn one() -> u32 { 1 }
fn fifteen() -> u32 { 15 }
fn ten() -> u64 { 10 }
fn ten_thousand() -> u64 { 10_000 }

// ---- materializers ----
fn shaped_i64(spec: &Spec) -> Vec<i64> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut v: Vec<i64> = match spec.shape.as_str() {
        "all_equal" => vec![7; n],
        "dup_heavy" => {
            let pool: Vec<i64> =
                (0..(n / 16).max(1)).map(|_| r.next() as i64).collect();
            (0..n).map(|_| pool[(r.next() as usize) % pool.len()]).collect()
        }
        _ => (0..n).map(|_| r.next() as i64).collect(),
    };
    if spec.shape == "sorted" {
        v.sort_unstable();
    } else if spec.shape == "reversed" {
        v.sort_unstable();
        v.reverse();
    }
    v
}

fn rand_string(r: &mut Rng, len: usize) -> String {
    (0..len)
        .map(|_| char::from(b'a' + (r.next() % 26) as u8))
        .collect()
}

fn gen_list_str(spec: &Spec) -> Vec<String> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut v: Vec<String> = match spec.shape.as_str() {
        "all_equal" => vec!["xxxxxxxx".to_string(); n],
        "dup_heavy" => {
            let pool: Vec<String> =
                (0..(n / 16).max(1)).map(|_| rand_string(&mut r, 8)).collect();
            (0..n)
                .map(|_| pool[(r.next() as usize) % pool.len()].clone())
                .collect()
        }
        _ => (0..n).map(|_| rand_string(&mut r, 8)).collect(),
    };
    if spec.shape == "sorted" {
        v.sort();
    } else if spec.shape == "reversed" {
        v.sort();
        v.reverse();
    }
    v
}

fn gen_string(spec: &Spec) -> String {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    match spec.shape.as_str() {
        "all_equal" => "a".repeat(n),
        "dup_heavy" => (0..n)
            .map(|_| char::from(b'a' + (r.next() % 4) as u8))
            .collect(),
        "sorted" | "reversed" => {
            let mut b: Vec<u8> =
                (0..n).map(|_| b'a' + (r.next() % 26) as u8).collect();
            b.sort_unstable();
            if spec.shape == "reversed" {
                b.reverse();
            }
            String::from_utf8(b).unwrap()
        }
        _ => rand_string(&mut r, n),
    }
}

fn gen_bytes(spec: &Spec) -> Vec<u8> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut v: Vec<u8> = match spec.shape.as_str() {
        "all_equal" => vec![b'a'; n],
        "dup_heavy" => (0..n).map(|_| b'a' + (r.next() % 4) as u8).collect(),
        _ => (0..n).map(|_| r.next() as u8).collect(),
    };
    if spec.shape == "sorted" {
        v.sort_unstable();
    } else if spec.shape == "reversed" {
        v.sort_unstable();
        v.reverse();
    }
    v
}

fn gen_map_ii(spec: &Spec) -> std::collections::HashMap<i64, i64> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut m = std::collections::HashMap::with_capacity(n);
    let pool: Vec<i64> = if spec.shape == "dup_heavy" {
        (0..(n / 16).max(1)).map(|_| r.next() as i64 % 64).collect()
    } else {
        Vec::new()
    };
    if spec.shape == "sorted" {
        for i in 0..n {
            m.insert(i as i64, r.next() as i64);
        }
        return m;
    }
    while m.len() < n {
        let v = if pool.is_empty() {
            r.next() as i64
        } else {
            pool[(r.next() as usize) % pool.len()]
        };
        m.insert(r.next() as i64, v);
    }
    m
}

fn gen_map_si(spec: &Spec) -> std::collections::HashMap<String, i64> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut m = std::collections::HashMap::with_capacity(n);
    let pool: Vec<i64> = if spec.shape == "dup_heavy" {
        (0..(n / 16).max(1)).map(|_| r.next() as i64 % 64).collect()
    } else {
        Vec::new()
    };
    if spec.shape == "sorted" {
        for i in 0..n {
            m.insert(format!("k{i:012}"), r.next() as i64);
        }
        return m;
    }
    while m.len() < n {
        let v = if pool.is_empty() {
            r.next() as i64
        } else {
            pool[(r.next() as usize) % pool.len()]
        };
        m.insert(format!("k{:015x}", r.next()), v);
    }
    m
}

fn gen_int(spec: &Spec) -> i64 {
    spec.size as i64
}

fn gen_bool(spec: &Spec) -> bool {
    spec.size != 0
}

fn gen_duration(spec: &Spec) -> std::time::Duration {
    std::time::Duration::from_millis(spec.size)
}

fn gen_list_f64(spec: &Spec) -> Vec<f64> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let unit = |r: &mut Rng| (r.next() >> 11) as f64 / (1u64 << 53) as f64;
    let mut v: Vec<f64> = match spec.shape.as_str() {
        "all_equal" => vec![0.5; n],
        "dup_heavy" => {
            let pool: Vec<f64> =
                (0..(n / 16).max(1)).map(|_| unit(&mut r)).collect();
            (0..n).map(|_| pool[(r.next() as usize) % pool.len()]).collect()
        }
        _ => (0..n).map(|_| unit(&mut r)).collect(),
    };
    if spec.shape == "sorted" {
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    } else if spec.shape == "reversed" {
        v.sort_by(|a, b| b.partial_cmp(a).unwrap());
    }
    v
}

fn gen_list_list(spec: &Spec) -> Vec<Vec<i64>> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    match spec.shape.as_str() {
        "all_equal" => vec![vec![7; 16]; n],
        "dup_heavy" => {
            let pool: Vec<Vec<i64>> = (0..(n / 16).max(1))
                .map(|_| (0..16).map(|_| r.next() as i64).collect())
                .collect();
            (0..n)
                .map(|_| pool[(r.next() as usize) % pool.len()].clone())
                .collect()
        }
        _ => (0..n)
            .map(|_| (0..16).map(|_| r.next() as i64).collect())
            .collect(),
    }
}

fn gen_set(spec: &Spec) -> std::collections::HashSet<i64> {
    let n = spec.size as usize;
    let mut r = Rng(spec.seed);
    let mut s = std::collections::HashSet::with_capacity(n);
    while s.len() < n {
        s.insert(r.next() as i64);
    }
    s
}

fn resolve_half_of(inputs: &[Spec], sizes: &[u64], idx: usize) -> i64 {
    (sizes[idx] / 2) as i64
}

// ---- measurement ----
const BATCH_THRESHOLD_S: f64 = 10e-6;
const BATCH_TARGET_S: f64 = 200e-6;

struct Out {
    wall_seconds: Vec<f64>,
    warmup_seconds: f64,
    batched: bool,
    peak_alloc_bytes: Option<u64>,
    notes: Vec<String>,
}

fn run_measured<A, P: FnMut() -> A, F: FnMut(A)>(
    req: &CallReq,
    owns: bool,
    mut prep: P,
    mut call: F,
) -> Out {
    let started = Instant::now();
    let budget_s = req.budget_ms as f64 / 1000.0;
    let mut out = Out {
        wall_seconds: vec![],
        warmup_seconds: 0.0,
        batched: false,
        peak_alloc_bytes: None,
        notes: vec![],
    };
    // warmup: the first-ever call, individually timed (memoizer signal)
    let a = prep();
    let w = Instant::now();
    call(a);
    out.warmup_seconds = w.elapsed().as_secs_f64();
    for _ in 1..req.warmup {
        call(prep());
    }

    let measure_time = req.measure.is_empty()
        || req.measure.iter().any(|m| m == "time");
    if measure_time {
        let a = prep();
        let t = Instant::now();
        call(a);
        let first = t.elapsed().as_secs_f64();
        let mut batch: u32 = 1;
        if first < BATCH_THRESHOLD_S && !owns {
            batch = ((BATCH_TARGET_S / first.max(1e-9)) as u32).clamp(1, 10_000);
            out.batched = true;
        }
        // batch==1: prep (clones for owned args) stays OUTSIDE the window;
        // batching only engages for borrow-style arms where prep is trivial
        let timed_rep = |prep: &mut P, call: &mut F, batch: u32| -> f64 {
            if batch == 1 {
                let a = prep();
                let t = Instant::now();
                call(a);
                t.elapsed().as_secs_f64()
            } else {
                let t = Instant::now();
                for _ in 0..batch {
                    call(prep());
                }
                t.elapsed().as_secs_f64() / batch as f64
            }
        };
        out.wall_seconds.push(if batch == 1 {
            first
        } else {
            timed_rep(&mut prep, &mut call, batch)
        });
        let mut total = out.wall_seconds[0] * batch as f64;
        let min_total = req.min_total_ms as f64 / 1000.0;
        while (out.wall_seconds.len() as u32) < req.max_repeats && total < min_total {
            if started.elapsed().as_secs_f64() > budget_s {
                out.notes.push("budget".into());
                break;
            }
            let t = timed_rep(&mut prep, &mut call, batch);
            out.wall_seconds.push(t);
            total += t * batch as f64;
        }
    }

    if req.measure.iter().any(|m| m == "memory") {
        let a = prep();
        let base = reset_peak();
        call(a);
        out.peak_alloc_bytes =
            Some(PEAK.load(Ordering::Relaxed).saturating_sub(base) as u64);
    }
    out
}

fn result_json(req: &CallReq, out: Out) -> Value {
    json!({
        "op": "result", "id": req.id, "fid": req.fid,
        "wall_seconds": out.wall_seconds,
        "warmup_seconds": out.warmup_seconds,
        "batched": out.batched,
        "peak_alloc_bytes": out.peak_alloc_bytes,
        "ret_deepsize": null,
        "mutates": false,
        "repeats_done": out.wall_seconds.len(),
        "notes": out.notes,
    })
}

fn error_json(id: &str, fid: &str, kind: &str, message: &str) -> Value {
    json!({"op": "error", "id": id, "fid": fid, "kind": kind,
           "message": message, "detail": {}, "retryable": false})
}

static PANIC_MSG: Mutex<String> = Mutex::new(String::new());

// ==== GENERATED DISPATCH ====
fn dispatch(req: &CallReq) -> Value {
    let sizes: Vec<u64> = req.inputs.iter().map(|s| s.size).collect();
    match req.fid.as_str() {
        // {{DISPATCH_ARMS}}
        _ => error_json(&req.id, &req.fid, "not_found",
                        &format!("unknown fid {}", req.fid)),
    }
}
// ==== END GENERATED ====

fn main() {
    // protocol integrity: private dup of stdout; fd1 -> stderr
    let proto_fd = unsafe { libc::dup(1) };
    unsafe { libc::dup2(2, 1) };
    let mut proto = unsafe {
        <std::fs::File as std::os::unix::io::FromRawFd>::from_raw_fd(proto_fd)
    };
    panic::set_hook(Box::new(|info| {
        *PANIC_MSG.lock().unwrap() = info.to_string();
    }));

    let hello = json!({
        "op": "hello", "protocol": 1, "language": "rust",
        "runtime": "rustc-built harness for {{TARGET_CRATE}}",
        "capabilities": {
            "spec_types": ["list_int", "list_float", "list_str",
                            "list_list_int", "str_", "bytes_", "set_int",
                            "int_mag", "dict_si", "dict_ii", "bool_",
                            "duration_ms"],
            "shapes": ["random", "sorted", "reversed", "dup_heavy",
                        "all_equal", "magnitude"],
            "memory": "counting_allocator",
            "discover": false,
        }
    });
    writeln!(proto, "{}", hello).unwrap();

    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let op = msg.get("op").and_then(|v| v.as_str()).unwrap_or("");
        let resp = match op {
            "shutdown" => break,
            "ping" => json!({"op": "pong", "id": msg.get("id")}),
            "call" => match serde_json::from_value::<CallReq>(msg.clone()) {
                Ok(req) => {
                    match panic::catch_unwind(AssertUnwindSafe(|| dispatch(&req))) {
                        Ok(v) => v,
                        Err(_) => error_json(
                            &req.id, &req.fid, "exception",
                            &format!("panicked: {}", PANIC_MSG.lock().unwrap()),
                        ),
                    }
                }
                Err(e) => {
                    let id = msg.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                    error_json(id, "", "internal", &format!("bad call: {e}"))
                }
            },
            _ => {
                let id = msg.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                error_json(id, "", "internal", &format!("unknown op {op:?}"))
            }
        };
        writeln!(proto, "{}", resp).unwrap();
    }
}
