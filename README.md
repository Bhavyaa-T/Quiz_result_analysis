# Quiz_result_analysis

Quick start
- Ensure `quizzes.csv` and `results.csv` are in this folder.
- Set your Perplexity API key in the environment as `PPLX_API_KEY` (or `PERPLEXITY_API_KEY`).
- Run the analyzer:

  - Print prompts without calling the API:
    `python analyze_results_with_perplexity.py --dry-run`

  - Call Perplexity (Sonar Pro) and save analyses:
    `python analyze_results_with_perplexity.py --model sonar-pro`

Node.js version (JS SDK)
- Requires Node 18+ (global fetch) and optionally the OpenAI client library.
- Install the SDK (optional; script falls back to fetch if not installed):
  `npm i openai`
- Run:
  - Dry run: `node analyze_results_with_perplexity.mjs --dry-run`
  - Call API: `node analyze_results_with_perplexity.mjs --model sonar-pro`

Outputs
- Per result, files are written under `analyses/<result_id>/`:
  - `prompt.json`: payload that would be sent to Perplexity
  - `response.json`: raw API response (when API key provided)
  - `analysis.md`: assistant’s markdown analysis (extracted when present)

Notes
- The script derives sources from `quizzes.csv` per question and asks the model to prefer them when relevant.
- It trusts `actualValue` from `results.csv` rather than `quizzes.csv`.

Using a .env file
- Create a `.env` file in this folder (not committed) with:
  `PPLX_API_KEY=your_perplexity_api_key_here`
- The script auto-loads `.env` at runtime if present. You can also set `PERPLEXITY_API_KEY` instead.
- Environment variables already set in your shell take precedence over `.env`.
