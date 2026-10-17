---
title: "CS-43 Labelling Protocol"
subtitle: "Media content and retraction notices: human labels for validating the Stage 3 classifications"
date: 2026-09-19
---

# 中文快速说明（给标注员）

**你要做什么**：读表格里的每一条内容，按下面的规则从下拉框里选一个标签。7 个人标的是**同一批内容**，只是顺序不同，最后投票决定答案。

**三条铁律**

1. **独立完成，不和任何人讨论**，包括组员。有疑问就写在 `note` 列里，不要问别人。
2. **只根据表格里显示的文字判断**，不要点开链接去读原文，也不要上网查这篇论文。模型也只看这些文字，人和模型要比的是同样的材料。
3. **A 表不是"挑一个最像的描述"，是按顺序回答四个问题**，见第 3 节。从第一题开始，答"是"就停，写下那个标签。

**两个任务**

- **A 表（媒体内容，200 条）**：一条新闻或博客的标题和摘要。按顺序问：
  - **Q0 能不能读？** 空的、不到 10 个词、或者翻译工具也读不了 → `A0`。
    **"我拿不准"不算 A0** —— 能读就必须往下答。
  - **Q1 文中说撤稿"已经发生"了吗？** → `A2`。
    注意：**要求撤稿、建议撤稿、正在调查、发了勘误，都不算**，往下走。
  - **Q2 文中质疑的是"这篇论文"吗？** → `A3`。
    只骂作者、只讲机构、只讲这个领域，而没说到这篇论文 → 不算，往下走。
  - **Q3 文中说出了这篇论文"发现了什么"吗？** → `A1`。
    只是列出作者和标题、只是同一个话题 → 不算。
  - **Q4 以上都不是** → `A4`。
- **B 表（撤稿声明，200 条）**：一段撤稿声明原文。标出它写了哪些撤稿原因（可多选，在对应列选 `Y`），再选它写得清不清楚（C3–C0）。

**每条都要填 `evidence` 列**：从原文里复制几个关键词（最多 15 个词），说明你为什么这样选。只有选 A4、A0 或 C0 时可以不填。

**时间**：A 表每条约 1 分钟，B 表每条约 2 分钟，合计约 10 小时。请在规定日期前交回，可以分几天做完。

**正式开始前先试标**：先做 20 条试标（10 条 A、10 条 B），开会讨论分歧后再做正式的 400 条。

---

# 1. Purpose

Stage 3 classifies two kinds of text with a local language model:

- what downstream media say about a retracted paper;
- what the paper's retraction notice states.

The client asked for a labelling protocol for 5 or 7 students (an odd number, so that a majority vote decides), applied to 200-500 randomly sampled papers. Human majority labels are the reference against which the model's labels are measured (accuracy, macro-F1, confusion matrix). The model receives the **same text, the same categories and the same precedence rules** as the labellers.

# 2. Design

| | Task A: media content | Task B: retraction notice |
|---|---|---|
| Unit | one media item (news story or blog post) about the paper | the paper's retraction notice |
| Sample | 200 papers, one item each | 200 other papers |
| Text shown | the paper's title; the item's title and summary as recorded by Altmetric | the paper's title; the full notice text |
| Labels | one category (A0-A4) and an evidence phrase | stated reasons (R1-R9 or R0), clarity (C0-C3) and an evidence phrase |

- **Sampling.** Random, with a fixed seed, in R (`analysis/labelling_sample.R`).
  - Task A is stratified by source (news, blog) and by period (before, after retraction): 50 papers per cell.
  - Items posted on the day of retraction are excluded, so that every item is unambiguously before or after.
  - Task B is a simple random sample of papers whose notice text was extracted. It excludes the Task A papers, and a notice shared by several papers is drawn at most once.
- **Labellers.** 7, each labelling every item independently. Each labeller receives the items in a different random order. The coordinator does not label; they adjudicate ties.
- **Blinding.** Labellers do not see the item's date, its period, or the model's output.
- **Pilot.** A 20-item pilot (10 A, 10 B, not part of the main sample) is labelled and discussed first. The protocol may be revised after the pilot, and only then. Pilot items are never used to evaluate the model.

# 3. Task A: media content

> **v2, 2026-09-25.** The first version gave five descriptions and asked you to
> pick the one that fitted. Six labellers applied it to 200 items and agreed at
> Krippendorff's alpha **0.58**, below the level any conclusion can rest on.
> The disagreement was not carelessness: where the same person met the same
> text twice they gave the same answer 38 times out of 42. Each was applying
> their own reading of a rule that permitted several. One labeller chose A3
> once in 200 items; another chose it 45 times.
>
> The descriptions are therefore replaced by **four questions in a fixed
> order**. Work down from the top and stop at the first "yes". You are not
> choosing which description fits best; you are answering yes or no, in order.

Read the paper's title (what the paper claims), then the item's title and
summary. Then work down this list.

### Q0. Can you read it at all?

**Yes → A0.** The item text (title and summary together) is empty, is **fewer
than 10 words**, or is in a script you cannot read even after pasting it into a
translation tool.

**"I am not sure which label to give" is not A0.** If you can read the text you
must answer Q1-Q4. A0 is about the text being unavailable, not about you being
undecided. In v1 one labeller used A0 41 times and the others 12 to 14.

### Q1. Does the text say the retraction **has happened**?

**Yes → A2.** The text states that the paper has been, or has been decided to
be, retracted, withdrawn, removed by the journal, or placed under an expression
of concern — *retracted, retraction, withdrawn, pulled, removed by the journal,
expression of concern*, 撤稿, 撤回, 撤销, 取り下げ.

**It must have happened or been decided.** These are **not** A2 — go to Q2:

| The text says | Not A2, because |
|---|---|
| "Scientists are calling for the paper to be retracted" | a demand, not an act |
| "The committee recommended retracting 10 papers" | a recommendation, not an act |
| "There is a petition to retract" | a demand |
| "The journal issued an erratum" / 勘误 | a correction is not a retraction |
| "The journal is investigating" | an investigation is not a retraction |

**This applies whatever else the item says.** An item that praises the finding
*and* reports that it was retracted is A2.

*Why first, and why this concrete:* in v1 every item whose text carried an
explicit retraction word had at least one labeller choose A2, and no item
without one was unanimously A2. The word is a reliable trigger. What 34 items
disagreed about was whether a *demand* counts. It does not.

### Q2. Does the text raise doubt about **this paper**?

**Yes → A3.** It questions or corrects this paper — its data, methods, images,
results or integrity. This includes an investigation into it, a failed
replication, allegations about it, an erratum or correction to it, and a demand
that it be retracted.

**It must be about this paper, not only about the people who wrote it.** An
item about a researcher's misconduct, a prison sentence or an institution's
record is not A3 unless the text also says something about this paper.

| The text | Answer |
|---|---|
| "Figure 3 of the study appears duplicated" | A3 |
| "Hundreds of scholars demand the study be retracted" | A3 |
| "Chinese scientist who edited embryos sentenced to prison", this paper not discussed | not A3 — continue |

### Q3. Does the text say what **this paper** found or claimed?

**Yes → A1.** The text reports a result, a conclusion or a recommendation —
whether in the item's own words or by reproducing the paper's own title or
abstract.

**No if the text only names the work.** A citation string, a reference-list
entry, an author list, or an item on the same subject that never says what this
paper found — none of those state a finding.

| The text | Answer |
|---|---|
| "A poop transplant could boost the effects of immunotherapy" | A1 — a finding is stated |
| "Abstract: The novel tetraaza-macrobicyclic chelator..." | A1 — the paper's own claim |
| "Woodworth, M. H., et al. Fecal microbiota transplantation..." | not A1 — a citation |
| "Memrise launches immersive language learning music lessons" | not A1 — a product, no finding |

*Why worded this way:* A1 against A4 was the largest disagreement in v1, 55
items of 200. The old wording asked whether the item "presents the finding as
valid", which was read as "is this item about the same subject". It is not.
The test is narrower: **does the text state what was found?**

### Q4. Otherwise → A4.

The item names the paper, lists it, or is about something else.

**Other rules**

- Judge only the text shown. Do not open the link, and do not use what you know about the paper from elsewhere.
- A summary that is cut off mid-sentence is still judged on what is there.
- Non-English text: you may paste the shown text into a translation tool. If you do, put `Y` in the `translated` column.
- `evidence`: copy the words that decided your answer (up to 15). Required for A1-A3. If you answered A2 it should be the words saying it happened; if A1, the words stating the finding. This is what makes a disagreement inspectable instead of a difference of opinion nobody can examine.

# 4. Task B: retraction notice

Read the notice. Then:

**B1. Stated reasons.** Put `Y` in every column that applies. Label only what the notice **states**, not what you suspect.

| Code | Reason group | Includes |
|---|---|---|
| R1 | Fabrication or falsification | fabricated or falsified data or results |
| R2 | Image problems | duplicated, manipulated or reused figures or images |
| R3 | Plagiarism | copied text, ideas or data from others; self-plagiarised text |
| R4 | Duplicate publication | the same work published twice or submitted in parallel |
| R5 | Peer review problems | compromised, manipulated or fake peer review; fake reviewer accounts |
| R6 | Paper mill or generated content | paper mill, computer-generated or nonsensical text, systematic manipulation, citation manipulation |
| R7 | Honest error | errors in data, analysis, methods or results, not attributed to misconduct; failure to reproduce |
| R8 | Authorship, ethics or consent | authorship disputes, missing ethics approval, no patient consent, undisclosed conflicts of interest, legal issues |
| R9 | Other | a stated reason that fits none of the above (write it in `note`) |
| R0 | No reason stated | the notice gives no reason at all. Use R0 alone, never with R1-R9. |

**B2. Clarity.** Choose one.

| Code | Clarity | Choose it when the notice... |
|---|---|---|
| **C3** | Clear | names the specific problem and says why it undermines the paper, e.g. "Figure 3B duplicates Figure 2A of another article". |
| **C2** | Partial | names a kind of problem without specifics, e.g. "concerns were raised about the data". |
| **C1** | Vague | uses only generic language that identifies no problem, e.g. "following an investigation", "at the request of the authors", "for reasons beyond our control". |
| **C0** | No reason | gives no reason, e.g. "This article has been retracted." Always pair it with R0. |

**B3. `evidence`**: copy the words that state the reason (up to 15). It is required unless the answer is R0/C0.

Who asked for the retraction (authors, editor, institution) is not a reason. A notice saying only "retracted at the authors' request" is **R0 with C1**.

# 5. Procedure and timetable

| Step | Who | When |
|---|---|---|
| Send protocol, pilot sheets (20 items) | coordinator | Day 0 |
| Label the pilot independently | 7 labellers | Day 1-2 |
| Pilot meeting: go through disagreements, revise the protocol if needed | all | Day 3 |
| Send the main sheets (400 items) | coordinator | Day 3 |
| Label the main sample independently | 7 labellers | Day 3-10 |
| Collect sheets; compute agreement and majority labels in R | coordinator | Day 11 |

**Returning the sheet.** Do not rename the file, and do not change, delete or reorder the columns or rows. Fill only these columns:
- Task A: `label`, `evidence`, `translated`, `note`;
- Task B: `R1`-`R0`, `clarity`, `evidence`, `note`.

# 6. Analysis (coordinator, in R)

- **Agreement between labellers**
  - Krippendorff's alpha, for A (nominal), for each reason column in B (binary) and for clarity (ordinal);
  - Fleiss' kappa as a secondary measure;
  - percent agreement.
- **Reference label**
  - Task A and clarity: the category with the most votes. A tie between top categories is decided by the coordinator, and every adjudicated item is recorded.
  - Reasons: `Y` when at least 4 of 7 labellers marked it.
- **Model against reference**: accuracy, macro-F1 and a confusion matrix for A and clarity; per-reason precision, recall and F1 for B1. Each model label carries its own stated reason, as the client required.
- **Retraction Watch labels**: RW's reason labels are mapped to R1-R9 by a fixed table (`analysis/rw_reason_map.csv`) and compared with the human and model labels.
- All labels, adjudications and scores are kept as files. The client's R run reads them and reproduces every table.

# 7. Why these choices

- **The same items for all labellers.** A majority vote needs several independent labels per item. Splitting the items would leave one label each.
- **Only the shown text.** The model sees only the Altmetric title and summary. A human who read the full article would be judging different evidence.
- **Precedence A2 > A3 > A1.** The client's question is whether the retracted status reaches the reader. Any mention of the retraction answers it, whatever else the item says.
- **Stated reasons only.** The comparison with Retraction Watch is about what the notice tells a reader, not about what actually happened.
