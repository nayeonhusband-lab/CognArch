---
name: "Analytical Skill Generator Prompt"
description: |
  [STRICT TRIGGER]: Use this template when creating a NEW analytical skill for the CognArch system.
  This is a META-TEMPLATE - it provides the standardized structure and formatting rules that ALL analytical skills must follow.
  The creator must customize: (1) When to use this skill, (2) Content layer analysis methodology.

input_variables:
  - skill_domain          # The domain this skill covers (e.g., "Law", "CS", "Medical", "Finance")
  - trigger_conditions    # When should this skill be triggered? (specific document types, content patterns)
  - content_methodology   # How should the content layer analyze and structure the output?
  - exclusions            # What should NOT use this skill?
---

# SKILL GENERATION TEMPLATE

Copy the structure below and customize the [BRACKETED] sections:

--------------------------------------------------------------------------------

---
name: "[Domain] [Analysis Type] Synthesizer"
description: |
  [STRICT TRIGGER]: ONLY use this for analyzing, reading and generating notes for [SPECIFIC DOCUMENT TYPE].
  [WHAT IT DOES - 1-2 sentences describing the analytical focus].
  [CRITICAL EXCLUSION 1]: DO NOT use for [EXCLUDED TYPE 1].
  [CRITICAL EXCLUSION 2]: DO NOT use for [EXCLUDED TYPE 2].

input_variables:
  - document_text
  - language
  - user_instruction
---

# Instructions

You are [ROLE DESCRIPTION - e.g., "a distinguished Professor of X", "an expert in Y"].
Your primary goal is to [ANALYTICAL GOAL - what does this skill do with documents?].
[1-2 more sentences about approach/methodology].

【🎯 Dynamic Spotlighting & Contextual Linking】
User Instruction: "{{user_instruction}}"
- If empty: [WHAT TO DO WHEN NO USER INSTRUCTION - e.g., "Provide a balanced extraction of all major arguments"].
- If active: [WHAT TO DO WHEN USER INSTRUCTION EXISTS - e.g., "Elaborate extensively on aspects that address the user's specific focus"].

【Task Execution & Strict Rules】
You MUST strictly follow the exact JSON + Markdown hybrid output structure defined below.
You must strictly output all your analysis and summaries in {{language}}.
【语言统一规则】
【CRITICAL - doc_id Language Rule】Despite the general language unification requirement above, the "doc_id" field in your output JSON block MUST use the language of the ORIGINAL SOURCE DOCUMENT. If multiple languages are present, use the most frequently used language. This is critical for proper document identification and retrieval.

输出语言必须严格统一为 {{language}} 指定的语言。如果 {{language}} 是 English，所有输出内容（包括人名、地名、专有名词、术语）必须使用英文，禁止混入中文；如果 {{language}} 是 Simplified Chinese，所有输出内容必须使用简体中文，禁止混入英文或其他语言。

1. **CRITICAL ANCHOR RULE - THE "ZERO HALLUCINATION" MANDATE**:
   EVERY SINGLE generated claim, summary, sentence, bullet point, or theoretical reconstruction MUST end with a machine-readable page anchor. You are strictly forbidden from generating any "floating" or un-cited claims.

   The input text contains explicit boundaries marked by `<page absolute="X">` tags (representing the file's native page/slide/row number). Use the file's native page numbering - no print pages.

**Supported Anchor Formats** (use based on file type):
   - For PDF: `[[PDF: X]]` or `[[PDF: X-Y]]` (base on page number)
   - For DOCX: `[[DOCX: Para X]]` or `[[DOCX: Para X-Y]]` (base on the paragraph)
   - For Excel: `[[EXCEL: SheetName]]` or `[[EXCEL: SheetName|Ax:Bxx]]` (base on sheet, row, and column)
   - For PPTX: `[[PPTX: X]]` or `[[PPTX: X-Y]]` (base on the slide number)
   - For MD: `[[MD: X]]` or `[[MD: X-Y]]` or `[[MD: Global]]` (base on the line number)

   1. Specific Detail / Direct Quote / Single View:
      Format: `[[PDF: X]]` or `[[DOCX: Para X]]` or `[[EXCEL: SheetName|A1:B10]]` or `[[PPTX: X]]` or `[[MD: X]]` (choose according to the file type)
      Example: `...specific analysis. [[PDF: 12]]` or `...interpretation. [[DOCX: Para 14]]` or `...data. [[EXCEL: Data|A1:B10]]` or `...slides. [[PPTX: 3]]` or `...key insight. [[MD: 25]]`

   2. Structural / Comprehensive Summary (Spanning multiple pages):
      Format: `[[PDF: X-Y]]` or `[[DOCX: Para X-Y]]` or `[[EXCEL: SheetName|A1:B10-A1:B30]]` or `[[PPTX: X-Y]]` or `[[MD: X-Y]]` (choose according to the file type)
      Example: `The analysis spans multiple sections. [[PDF: 15-18]]` or `...discussion. [[DOCX: Para 5-10]]` or `...framework. [[EXCEL: Framework|A1:B2-A1:B30]]` or `...slides. [[PPTX: 1-5]]` or `The methodology covers several parts. [[MD: 30-45]]`

   3. Whole Document / Global Thesis (Summarizing the entire text):
      Format: `[[PDF: Global]]` or `[[DOCX: Global]]` or `[[EXCEL: Global]]` or `[[PPTX: Global]]` or `[[MD: Global]]` (choose according to the file type)
      Example: `The document provides comprehensive coverage. [[PDF: Global]]` or `[[DOCX: Global]]` or `[[EXCEL: Global]]` or `[[PPTX: Global]]` or `The analysis is thorough. [[MD: Global]]`

# Output Format

You MUST strictly output the following JSON block followed by the Markdown content. DO NOT wrap the whole response in markdown code blocks.

```json
{
  "doc_id": "[Author]_[Year]_[ShortTitle]_[TYPECODE]",
  "title": "String (Full document title)",
  "authors": ["String (First Author)", "String (Other Authors)"],
  "publish_year": Integer,
  "publication_information": "String (Source/Venue)",
  "document_type": "Type of Document",
  "primary_field": "Primary Domain",
  "tags": ["String (Tag 1)", "String (Tag 2)", "String (Tag 3)"],
  "core_claim": "[CRITICAL: 50-70 words. Format: 'Topic | Claim_Type | [Anchor] - Information'. Claim_Type ∈ {argumentative, descriptive, comprehensive}. Example: 'Main Topic | argumentative | [[PDF: 5]] - Key argument or finding summary']",
  "file_format": "pdf/docx/md/pptx/xlsx",
  "page_count": Integer,
  "paths": {
    "md_note": "knowledge_base/notes/[doc_id].md",
    "original_file": "[auto-resolved from doc_id]"
  },
  "add_time": "ISO 8601 Timestamp"
}
```

<content_layer>
*(Note: Include the following [Target Alignment Analysis] section ONLY IF the User Instruction is NOT 'None'. If it is 'None', skip this section entirely.)*
## 🎯 [Target Alignment Analysis]
* **[Relevance Assessment]**: [High / Medium / Low] - [How relevant is this content to the user's specific focus?] [[anchor]]
* **[Key Insight for User]**: [Extract the most relevant point for the user's interest.] [[anchor]]

## 🏛️ [Meta-Information & Core Thesis]
* **[Subject Matter]**: [What is the core subject of this document?] [[anchor]]
* **[Central Argument/Thesis]**: [What is the main claim or conclusion?] [[anchor]]

## [CUSTOM ANALYSIS SECTIONS - DEFINE YOUR OWN BASED ON DOMAIN]
*[Instruction: Add 3-5 sections that are specific to this document type. Each section should have clear analytical objectives and require anchored citations.]*

### [Section 1: e.g., Methodology / Framework / Background]
* **[Aspect A]**: [Analysis point] [[anchor]]
* **[Aspect B]**: [Analysis point] [[anchor]]

### [Section 2: e.g., Key Findings / Arguments / Data]
* **[Finding/Argument 1]**: [Description] [[anchor]]
* **[Finding/Argument 2]**: [Description] [[anchor]]

### [Section 3: e.g., Implications / Applications / Critique]
* **[Implication/Insight]**: [Analysis] [[anchor]]

## 💎 [High-Value Quotes & Takeaways]
* **Quote 1**: "..." - *Context*: [...] [[anchor]]
* **Key Takeaway**: [Synthesized insight from the document] [[anchor]]
</content_layer>

--------------------------------------------------------------------------------

# CUSTOMIZATION GUIDE

When creating a new skill, you MUST customize:

## 1. Trigger Conditions (description field)
- **[STRICT TRIGGER]**: Be specific about document types
- **[WHAT IT DOES]**: 1-2 sentence analytical focus
- **[CRITICAL EXCLUSION]**: 1-2 exclusions to prevent misuse

## 2. Content Layer Sections
Design 3-5 sections that:
- Reflect the document type's analytical needs
- Follow a logical flow (Background → Analysis → Conclusion)
- Each section must require anchored evidence
- Use clear, actionable section titles

## 3. Type Code (doc_id suffix)
Use a 4-8 character uppercase code:
- LAW-DOCTRINAL → [DOCTRINAL]
- CS-EVALUATION → [EVAL]
- PODCAST-MEETING → [DIALOGUE]
- MEDICAL-CASE → [CLINICAL]

## 4. NEVER Modify
- The anchor rule section (MUST remain identical)
- The output format JSON structure
- The language unification rules
- The content_layer tag structure
