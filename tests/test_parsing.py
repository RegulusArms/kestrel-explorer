"""The KESTREL_STATS report: sorted names, counts, averages and peaks."""
from common import check, finish

from kestrel import stats

# -- KESTREL_STATS's report
stats.enable()
stats.sample("b timing (ms)", 1.0)
stats.sample("b timing (ms)", 4.5)
stats.count("a count", 3)
for v in (2, 7, 5):
    stats.peak("c peak", v)
check(stats.summary() == "Kestrel stats (KESTREL_STATS):\n  a count: 3\n  b timing (ms): 2×, average 2.8, largest 4.5\n"
                         "  c peak: most 7\n",
      "the KESTREL_STATS report lists sorted names, counts, averages and peaks")
finish()
