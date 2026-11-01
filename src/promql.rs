use crate::*;
use promql_parser::{
    label::{MatchOp, Matcher},
    parser::{self, Expr, VectorSelector},
};

fn prepare_query(query: &str) -> Result<String> {
    ensure!(
        query.len() <= 65536,
        "metric query is too complex: at most 65536 bytes"
    );
    let (mut quote, mut escaped, mut comment, mut word) = (None, false, false, false);
    let (mut tokens, mut depth) = (0usize, 0usize);
    let mut normalized = String::with_capacity(query.len());
    let mut raw = String::new();
    for ch in query.chars() {
        if comment {
            normalized.push(ch);
            comment = ch != '\n';
            continue;
        }
        if let Some(delimiter) = quote {
            if delimiter == '`' {
                if ch == '`' {
                    // The upstream lexer processes escapes in backticks; PromQL raw strings do not.
                    normalized.push_str(&serde_json::to_string(&raw)?);
                    raw.clear();
                    quote = None;
                } else {
                    raw.push(ch);
                }
                continue;
            }
            normalized.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == delimiter {
                quote = None;
            }
            continue;
        }
        if ch != '`' {
            normalized.push(ch);
        }
        if ch == '#' {
            comment = true;
            word = false;
            continue;
        }
        if ch.is_whitespace() {
            word = false;
            continue;
        }
        let in_word = ch.is_ascii_alphanumeric() || "_:.".contains(ch);
        if !in_word || !word {
            tokens += 1;
        }
        word = in_word;
        match ch {
            '"' | '\'' | '`' => quote = Some(ch),
            '(' | '[' | '{' => depth += 1,
            ')' | ']' | '}' => depth = depth.saturating_sub(1),
            _ => {}
        }
        // Bound parser construction and recursive AST checks before either can exhaust the stack.
        ensure!(
            tokens <= 1024 && depth <= 128,
            "metric query is too complex: at most 1024 tokens and 128 nesting levels"
        );
    }
    ensure!(
        quote.is_none(),
        "metric query has an unterminated quoted string"
    );
    Ok(normalized)
}

fn selector(vector: &VectorSelector, keys: &[String], label: &str) -> Result<()> {
    let group = |matchers: &[Matcher]| -> Result<()> {
        let mut pinned = false;
        for matcher in matchers.iter().filter(|m| keys.contains(&m.name)) {
            let exact = match matcher.op {
                MatchOp::Equal => matcher.value == label,
                MatchOp::Re(_) => {
                    let escaped = regex::escape(label);
                    matcher.value == escaped || matcher.value == format!("^{escaped}$")
                }
                _ => false,
            };
            ensure!(
                exact,
                "metric selector reaches outside {label}; every selector must select only {label}"
            );
            pinned = true;
        }
        ensure!(
            pinned,
            "every metric selector must explicitly select {label}"
        );
        Ok(())
    };
    if vector.matchers.or_matchers.is_empty() {
        group(&vector.matchers.matchers)?;
    } else {
        if !vector.matchers.matchers.is_empty() {
            group(&vector.matchers.matchers)?;
        }
        for alternative in &vector.matchers.or_matchers {
            group(alternative)?;
        }
    }
    Ok(())
}

fn check(expr: &Expr, keys: &[String], label: &str) -> Result<usize> {
    match expr {
        Expr::VectorSelector(v) => selector(v, keys, label).map(|_| 1),
        Expr::MatrixSelector(v) => selector(&v.vs, keys, label).map(|_| 1),
        Expr::Aggregate(v) => {
            // Parameters such as topk(scalar(sum(metric)), ...) can also read data.
            let param = v
                .param
                .as_ref()
                .map(|p| check(p, keys, label))
                .transpose()?
                .unwrap_or(0);
            Ok(param + check(&v.expr, keys, label)?)
        }
        Expr::Unary(v) => check(&v.expr, keys, label),
        Expr::Binary(v) => Ok(check(&v.lhs, keys, label)? + check(&v.rhs, keys, label)?),
        Expr::Paren(v) => check(&v.expr, keys, label),
        Expr::Subquery(v) => check(&v.expr, keys, label),
        Expr::Call(v) => {
            // With no second selector, info() implicitly reads unscoped target_info.
            ensure!(
                v.func.name != "info" || v.args.args.len() == 2,
                "info() needs an explicit selector for {label}"
            );
            v.args
                .args
                .iter()
                .try_fold(0, |count, arg| Ok(count + check(arg, keys, label)?))
        }
        Expr::NumberLiteral(_) | Expr::StringLiteral(_) => Ok(0),
        Expr::Extension(_) => {
            bail!("cannot confirm the environment of an extended metric expression")
        }
    }
}

pub fn scoped(query: &str, keys: &[String], label: &str) -> Result<()> {
    let parsed_query = prepare_query(query)?;
    let expr = parser::parse(&parsed_query).map_err(|_| anyhow!("shared metrics queries must be valid PromQL so every selector can be checked for {label}"))?;
    ensure!(
        check(&expr, keys, label)? > 0,
        "this query needs a metric selector for {label}"
    );
    Ok(())
}
