# Primary sources and verification notes

Checked 19 October 2026. Use documentation matching the versions installed during T00. Links below support underlying facts/APIs; the specific sample, trial values, recovery rules and task estimates are proposed project choices rather than published validated standards. Do not copy reference text or tutorial code without attribution/license review.

Additional security research and primary sources are in `docs/SECURITY_SOURCES.md`, with mandatory implementation rules in `SECURITY.md` (kit v1.2).

## Astronomy and analysis

1. **Kovács, Zucker & Mazeh (2002), A box-fitting algorithm in the search for periodic transits.** https://arxiv.org/abs/astro-ph/0206099 and https://doi.org/10.1051/0004-6361:20020802 . Primary BLS method reference. This project uses an existing implementation; it does not invent BLS.
2. **Astropy BLS user guide.** https://docs.astropy.org/en/stable/timeseries/bls.html . Background for using a box-model period search.
3. **Astropy BoxLeastSquares API.** https://docs.astropy.org/en/stable/api/astropy.timeseries.BoxLeastSquares.html . Verifies period-grid generation, duration inputs, likelihood/SNR objectives, fast method, phase oversampling and returned diagnostics. The period-grid transit-count heuristic is not a count-of-observed-transits acceptance rule.
4. **Lightkurve search_lightcurve API.** https://lightkurve.github.io/lightkurve/reference/api/lightkurve.search_lightcurve.html . Search filters include mission, author, sector and exposure constraints. Resolve actual products, not assumed table positions.
5. **Lightkurve I/O API.** https://lightkurve.github.io/lightkurve/reference/api/lightkurve.io.read.html . TESS light-curve products, explicit flux columns and quality-bitmask behavior.
6. **Lightkurve light-curve file tutorial.** https://lightkurve.github.io/lightkurve/tutorials/1-getting-started/using-light-curve-file-products.html . Inspect data product metadata and distinguish SAP/PDCSAP. Use current classes rather than deprecated examples in old tutorials.
7. **Lightkurve Pi Mensae tutorial.** https://lightkurve.github.io/lightkurve/tutorials/3-science-examples/exoplanets-recover-first-tess-candidate.html . Establishes TIC 261136679, sector 1 and the approximately 6.27-day signal. The tutorial uses pixel data; TransitStress deliberately acquires extracted light-curve products instead.
8. **Gandolfi et al. (2018), TESS's first planet: a super-Earth transiting the naked-eye star Pi Mensae.** https://arxiv.org/abs/1809.07573 . Published-reference starting point for the known planet. Resolve/cite the exact reference ephemeris during the build; never mix epoch conventions.

   T03 checked 20 October 2026: version 2, Table 5 (PDF page 11) explicitly
   gives P=6.26834 +/- 0.00024 d and T0=8325.503055 +/- 0.00077 in
   BJD_TDB-2450000. `data/sanity_reference.json` stores the converted absolute
   epoch 2458325.503055 BJD_TDB for later post-search comparison only. This is
   a cited literature reference, not measured recovery or a search-grid input.
9. **NASA HEASARC TESS light-curve tutorial.** https://heasarc.gsfc.nasa.gov/docs/tess/LightCurveFile-Object-Tutorial.html . Describes upstream processing and SAP versus PDCSAP. Historical API names are not a dependency specification.
10. **NASA Exoplanet Archive simulated-data overview.** https://exoplanetarchive.ipac.caltech.edu/docs/KeplerSimulated.html . Context for mission-scale completeness/reliability studies. TransitStress does not reproduce their scope or derive their completeness results.

## Acquisition, rights and reproducibility

11. **MAST TESS data products.** https://archive.stsci.edu/missions-and-data/tess/data-products . Product naming/documentation and archive-manual links; verify exact acquisition fields against the live API during T02.
12. **MAST TESS bulk downloads.** https://archive.stsci.edu/tess/bulk_downloads.html . Another documented discovery/access route if needed; do not silently change the chosen product class.
13. **MAST data-use policy.** https://archive.stsci.edu/publishing/data-use . Product-specific rights and acknowledgment requirements; do not assume every archive collection has the same license.
14. **MAST mission acknowledgments.** https://archive.stsci.edu/publishing/mission-acknowledgements . Attribution guidance for TESS data. Do not imply the student received NASA funding or institutional sponsorship.
15. **MAST data attribution guidance.** https://archive.stsci.edu/publishing/data-attributions . Use persistent archive identifiers/DOIs where applicable to the actual selected products.
16. **Git commit documentation.** https://git-scm.com/docs/git-commit . Authentic local version history. Commits should record real development, not manufactured calendar activity.

## Implementation and product

17. **pandas rolling API.** https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.rolling.html . Time-offset windows and endpoint/minimum-observation behavior. Compare any implementation against the explicit time-distance reference in this specification.
18. **Streamlit theming.** https://docs.streamlit.io/develop/concepts/configuration/theming . Use supported theme controls rather than unstable DOM hacks.
19. **Streamlit AppTest.** https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest . App-level interaction/state testing; supplement with real-browser visual inspection.
20. **Streamlit caching.** https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data . Caching can reduce repeated work but correctness still depends on complete scientific cache keys. Never ingest an untrusted serialized Python object.
21. **Streamlit Community Cloud deployment.** https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy . GitHub-based app deployment. Host limits/compatibility must be tested on the actual release; this plan makes no promise of indefinite availability.
22. **pytest markers.** https://docs.pytest.org/en/stable/how-to/mark.html . Separate slow/network tests from deterministic offline checks.
23. **uv installation.** https://docs.astral.sh/uv/getting-started/installation/ . Official Windows installation routes, including WinGet. Resolve and lock actual package compatibility during setup.
24. **Python virtual environments.** https://docs.python.org/3/library/venv.html . Environment isolation background; uv manages the local environment in this workflow.

## Agent workflow and subscriptions

25. **OpenAI model selection and effort controls.** https://learn.chatgpt.com/docs/models . GPT-6.1 availability/controls depend on account, client and rollout. Start at moderate effort and increase for difficult work; there is no guarantee that Max prevents mistakes.
26. **OpenAI usage/pricing documentation.** https://learn.chatgpt.com/docs/pricing . Context, reasoning, model and task complexity affect usage; subscription allowances are not API token pricing.
27. **OpenAI repository guidance.** https://learn.chatgpt.com/docs/customization/overview . Keep AGENTS.md concise and combine instructions with enforced checks.
28. **Gemini CLI context files.** https://geminicli.com/docs/cli/gemini-md/ . GEMINI.md supplies project context; inspect loaded context rather than assuming another tool uses the same convention.
29. **Google Antigravity model controls.** https://www.antigravity.google/docs/models/ . Availability/thinking controls vary by plan and surface. A Google AI subscription tier is not a model name.
30. **Google Antigravity plans.** https://www.antigravity.google/docs/plans . Quotas and overage settings; use the actual account usage display and do not assume moving interfaces creates separate unlimited usage.

## Attribution and contribution note to complete at release

Record exact library versions/citations, the selected products' identifiers/rights, upstream code adaptations and their licenses, original code authorship, and AI's role in planning/coding/review. Claims about reviews, independent reproduction and users must be supported by real events. No professor/institution/mission endorsement is implied by public data use.
