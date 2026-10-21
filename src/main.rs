fn main() {
    let code = run();
    std::process::exit(code);
}
fn run() -> i32 {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.first().is_some_and(|a| a == "__demo-tool") {
        match reckon::demo::tool(
            args.get(1).map(String::as_str).unwrap_or(""),
            args.get(2..).unwrap_or(&[]),
        ) {
            Ok(v) => {
                println!("{v}");
                return 0;
            }
            Err(e) => {
                eprintln!("reckon: {e}");
                return 1;
            }
        }
    }
    let args = match reckon::cli::parse(args) {
        Ok(v) => v,
        Err(e) => {
            let code = e.exit_code();
            let _ = e.print();
            return code;
        }
    };
    if let Err(e) = reckon::process::signals() {
        eprintln!("reckon: could not install signal handler: {e}");
        return 1;
    }
    match reckon::cli::execute(&args) {
        Ok(result) => {
            let code = reckon::cli::exit_code(&args.command, &result);
            if result.get("_exit").is_none() {
                if args.flag("json") {
                    match serde_json::to_string_pretty(&result) {
                        Ok(text) => println!("{text}"),
                        Err(e) => {
                            eprintln!("reckon: {e}");
                            return 1;
                        }
                    }
                } else if let Err(e) = reckon::cli::display(&args.command, &result) {
                    eprintln!("reckon: {e}");
                    return 1;
                }
            }
            code
        }
        Err(e) => {
            eprintln!("reckon: {e}");
            1
        }
    }
}
