# Heavy tools only when needed

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings).*

**Run a heavy tool only when a measurement shows that you need its result.** A heavy tool is an external encoding, compression, rendering or build tool, or any other tool that uses a large share of the machine's CPU, memory or disk.

1. **Find out if the result can change your decision or your deliverable.** Before you run a heavy tool, check it with a measurement, a cheap test or a known limit, such as the formats that a browser supports. When the result cannot change the decision or the deliverable, do not run the tool.
2. **Do no unnecessary work that uses a large share of the CPU, the memory or the disk.**
   - Run a maximum of one heavy process at a time. Do not run heavy tools in parallel.
   - Limit the threads of the tool.
   - Start the tool with low process priority.
3. **Estimate the impact of each external tool before you use it.** Estimate its CPU, memory and disk use, its run time, and the number of processes that it starts. When the impact can be heavy, measure it on a small sample first. Then run the tool only within the limits of item 2.
