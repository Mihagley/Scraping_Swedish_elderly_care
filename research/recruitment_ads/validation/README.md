# Human validation protocol

1. Read the entire original description, not only highlighted language hits. Confirm occupation, elderly-care context and employer method before coding.
2. Enter `manual_required=true` only for a Swedish entry-proficiency requirement. Preferred abilities, training, workplace descriptions, application instructions and accepted alternative languages are not mandatory Swedish proficiency.
3. Enter `manual_formal=true` only for an explicitly required measurable language category (independent CEFR level, course/SFI threshold or Swedish language test). Do not invent equivalences.
4. Use `manual_category` for independent taxonomy categories and semantic statuses. Record uncertainty, employer/context errors, reviewer/date and adjudication in `notes`. Leave binary fields blank while unresolved.
5. Review the separate random predicted-negative sample using full text. Do not infer a zero false-negative rate from unreviewed cases.
6. A second independent coder and disagreement adjudication are recommended before using estimates analytically. Keep a held-out labelled set when rules change.

Real advertisements have no prefilled human labels. Synthetic unit fixtures exercise software behaviour; they are not the empirical gold standard. Samples live in ignored output/review directories so human coding is not accidentally replaced by repository updates.
