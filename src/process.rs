use crate::*;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

static CANCELLED: AtomicBool = AtomicBool::new(false);
pub fn signals() -> Result<()> {
    ctrlc::set_handler(|| CANCELLED.store(true, Ordering::SeqCst))?;
    Ok(())
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Ok,
    Failed,
    Timeout,
    OutputLimit,
    Unavailable,
    Cancelled,
}
impl Status {
    pub fn name(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Failed => "failed",
            Self::Timeout => "timeout",
            Self::OutputLimit => "output_limit",
            Self::Unavailable => "unavailable",
            Self::Cancelled => "cancelled",
        }
    }
}
#[derive(Debug, Clone)]
pub struct Output {
    pub status: Status,
    pub exit_code: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub duration_ms: u128,
}
pub type Environment = BTreeMap<String, String>;
pub fn run(
    argv: &[String],
    env: Option<&Environment>,
    cwd: Option<&Path>,
    timeout: f64,
    max_bytes: usize,
    stdin: Option<&[u8]>,
) -> Result<Output> {
    ensure!(
        timeout.is_finite()
            && timeout > 0.0
            && timeout <= 120.0
            && max_bytes > 0
            && max_bytes <= 8 * 1048576,
        "timeout must be 0–120 seconds; output limit 1–8388608 bytes"
    );
    run_bounded(argv, env, cwd, timeout, max_bytes, stdin)
}
pub fn run_bounded(
    argv: &[String],
    env: Option<&Environment>,
    cwd: Option<&Path>,
    timeout: f64,
    max_bytes: usize,
    stdin: Option<&[u8]>,
) -> Result<Output> {
    ensure!(
        !argv.is_empty() && timeout.is_finite() && timeout > 0.0 && max_bytes > 0,
        "invalid process limits"
    );
    let started = Instant::now();
    let mut command = Command::new(&argv[0]);
    command.args(&argv[1..]);
    if let Some(env) = env {
        command.env_clear().envs(env);
    }
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    command
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x00000200);
    }
    let mut child = match command.spawn() {
        Ok(c) => c,
        Err(e) => {
            return Ok(Output {
                status: Status::Unavailable,
                exit_code: None,
                stdout: vec![],
                stderr: e.to_string().into_bytes(),
                duration_ms: 0,
            });
        }
    };
    let (tx, rx) = mpsc::sync_channel(4);
    fn drain(
        mut pipe: impl Read + Send + 'static,
        stream: usize,
        tx: mpsc::SyncSender<(usize, Vec<u8>)>,
    ) {
        std::thread::spawn(move || {
            let mut bytes = [0; 8192];
            loop {
                match pipe.read(&mut bytes) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        if tx.send((stream, bytes[..n].to_vec())).is_err() {
                            return;
                        }
                    }
                }
            }
            let _ = tx.send((stream, vec![]));
        });
    }
    drain(
        child
            .stdout
            .take()
            .ok_or_else(|| anyhow!("missing stdout pipe"))?,
        0,
        tx.clone(),
    );
    drain(
        child
            .stderr
            .take()
            .ok_or_else(|| anyhow!("missing stderr pipe"))?,
        1,
        tx.clone(),
    );
    drop(tx);
    if let Some(bytes) = stdin {
        let bytes = bytes.to_vec();
        if let Some(mut pipe) = child.stdin.take() {
            std::thread::spawn(move || {
                let _ = pipe.write_all(&bytes);
            });
        }
    }
    let mut output = [Vec::new(), Vec::new()];
    let mut closed = 0;
    let mut status = Status::Ok;
    let mut exit = None;
    loop {
        if CANCELLED.load(Ordering::SeqCst) {
            status = Status::Cancelled;
            break;
        }
        if started.elapsed().as_secs_f64() >= timeout {
            status = Status::Timeout;
            break;
        }
        match rx.recv_timeout(Duration::from_millis(10)) {
            Ok((stream, bytes)) => {
                if bytes.is_empty() {
                    closed += 1;
                } else {
                    let remaining = max_bytes.saturating_sub(output[0].len() + output[1].len());
                    output[stream].extend_from_slice(&bytes[..bytes.len().min(remaining)]);
                    if bytes.len() > remaining {
                        status = Status::OutputLimit;
                        break;
                    }
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => closed = 2,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if exit.is_none() {
            exit = child.try_wait()?;
        }
        if exit.is_some() && closed >= 2 {
            break;
        }
    }
    // A parent can exit while descendants still own its pipes. Reap its process group too.
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let code = exit.or_else(|| child.wait().ok());
    let exit_code = code.and_then(|s| {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            s.code().or_else(|| s.signal().map(|n| -n))
        }
        #[cfg(not(unix))]
        {
            s.code()
        }
    });
    if status == Status::Ok && exit_code != Some(0) {
        status = Status::Failed;
    }
    Ok(Output {
        status,
        exit_code,
        stdout: std::mem::take(&mut output[0]),
        stderr: std::mem::take(&mut output[1]),
        duration_ms: started.elapsed().as_millis(),
    })
}
