# Report status

report.zh.md contains Chinese sections 1-8. It is a working report, not the final PDF. Abstract, AI disclosure, appendix, author details and final English/PDF production remain to be completed.

The main report uses results/primary. See replication.zh.md for the later full rerun; do not replace individual main-report numbers with rerun values. Assets include figures and evidence. Evidence JSON files preserve original historical path labels; results/index.json maps their batch identifiers to current locations.

Optional figure regeneration: install matplotlib==3.7.2, then run `python report/assets/build_section5.py` from the repository root. This rewrites section 5 and figure assets; it is not required to reproduce database experiments. Chinese fonts are needed for Chinese figures.
