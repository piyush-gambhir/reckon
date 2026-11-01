use crate::*;

fn number(value: &Value) -> Option<f64> {
    value.as_f64().or_else(|| {
        let value = value.as_str()?;
        match value {
            "NaN" => Some(f64::NAN),
            "Inf" | "+Inf" => Some(f64::INFINITY),
            "-Inf" => Some(f64::NEG_INFINITY),
            _ => value.parse::<f64>().ok().filter(|n| n.is_finite()),
        }
    })
}

fn pair(value: &Value) -> Result<&[Value]> {
    let pair = value
        .as_array()
        .ok_or_else(|| anyhow!("invalid metric sample"))?;
    ensure!(
        pair.len() == 2 && pair[0].as_f64().is_some_and(f64::is_finite),
        "invalid metric sample"
    );
    Ok(pair)
}

fn histogram(value: &Value) -> Result<()> {
    ensure!(
        value.is_object() && number(&value["count"]).is_some() && number(&value["sum"]).is_some(),
        "invalid histogram sample"
    );
    if let Some(buckets) = value.get("buckets") {
        let buckets = buckets
            .as_array()
            .ok_or_else(|| anyhow!("invalid histogram buckets"))?;
        for bucket in buckets {
            let bucket = bucket
                .as_array()
                .ok_or_else(|| anyhow!("invalid histogram bucket"))?;
            ensure!(
                bucket.len() == 4
                    && bucket[0].as_u64().is_some_and(|rule| rule <= 3)
                    && bucket[1..].iter().all(|v| number(v).is_some()),
                "invalid histogram bucket"
            );
        }
    }
    Ok(())
}

pub fn matrix_summary(series: &Value) -> Result<Value> {
    let series = series
        .as_array()
        .ok_or_else(|| anyhow!("invalid metric series"))?;
    let mut stats = vec![];
    let mut total_samples = 0;
    let mut total_nonfinite = 0;
    let mut total_histograms = 0;
    let mut unreported_series = 0;
    // Validate every series, even when only its first 20 are summarized.
    for (index, entry) in series.iter().enumerate() {
        ensure!(entry.is_object(), "invalid metric series");
        let samples_unreported = entry.get("values").is_none() && entry.get("histograms").is_none();
        unreported_series += usize::from(samples_unreported);
        let labels = match entry.get("metric") {
            None | Some(Value::Null) => None,
            Some(labels) => Some(
                labels
                    .as_object()
                    .ok_or_else(|| anyhow!("invalid metric labels"))?,
            ),
        };
        if let Some(labels) = labels {
            ensure!(
                labels.values().all(Value::is_string),
                "invalid metric labels"
            );
        }
        let mut values = vec![];
        let mut nonfinite = 0;
        let mut histograms = 0;
        if let Some(samples) = entry.get("values") {
            let samples = samples
                .as_array()
                .ok_or_else(|| anyhow!("invalid metric samples"))?;
            for sample in samples {
                let value =
                    number(&pair(sample)?[1]).ok_or_else(|| anyhow!("invalid metric value"))?;
                if value.is_finite() {
                    values.push(value);
                } else {
                    nonfinite += 1;
                }
            }
        }
        if let Some(samples) = entry.get("histograms") {
            let samples = samples
                .as_array()
                .ok_or_else(|| anyhow!("invalid histogram samples"))?;
            for sample in samples {
                histogram(&pair(sample)?[1])?;
                histograms += 1;
            }
        }
        total_samples += values.len() + nonfinite + histograms;
        total_nonfinite += nonfinite;
        total_histograms += histograms;
        if index >= 20 {
            continue;
        }
        let mut shown_labels = serde_json::Map::new();
        let mut labels_truncated = labels.is_some_and(|labels| labels.len() > 20);
        for (key, value) in labels.into_iter().flat_map(|labels| labels.iter()).take(20) {
            if key.chars().take(81).count() > 80 {
                labels_truncated = true;
                continue;
            }
            let value = text(value);
            let shown = value.chars().take(200).collect::<String>();
            labels_truncated |= shown.len() < value.len();
            shown_labels.insert(key.clone(), json!(shown));
        }
        // Scale before summing so finite inputs near f64::MAX keep a finite mean.
        let scale = values.iter().map(|v| v.abs()).fold(0.0, f64::max);
        let mean = if values.is_empty() {
            None
        } else if scale == 0.0 {
            Some(0.0)
        } else {
            Some(values.iter().map(|v| v / scale).sum::<f64>() / values.len() as f64 * scale)
        };
        stats.push(json!({"labels":shown_labels,"labels_truncated":labels_truncated,"samples":values.len(),
            "samples_unreported":samples_unreported,
            "non_finite_samples":nonfinite,"histogram_samples":histograms,
            "min":values.iter().copied().reduce(f64::min),"max":values.iter().copied().reduce(f64::max),"mean":mean}));
    }
    let missing = if unreported_series > 0 {
        format!("{unreported_series} series omit sample fields, so their coverage is unknown. ")
    } else {
        String::new()
    };
    Ok(
        json!({"summary":format!("{} metric series; {total_nonfinite} non-finite float samples and {total_histograms} histogram samples captured. {missing}Finite float means are descriptive, not traffic-weighted aggregates. Summaries show at most 20 series and 20 labels per series; inspect the artifact for captured data.",series.len()),
        "completeness":if unreported_series>0 {"partial"} else if total_samples>0 {"bounded"} else {"empty"},"series":stats,"series_truncated":series.len()>20}),
    )
}
