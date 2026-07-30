# SkillX: Automatically Constructing Skill Knowledge Bases for Agents

Chenxi Wang <sup>*</sup> <sup>1</sup> <sup>2</sup> Zhuoyun Yu <sup>*</sup> <sup>1</sup> <sup>2</sup> Xin Xie <sup>
2</sup> Wuguannan Yao <sup>2</sup> Runnan Fang <sup>1</sup> Shuofei Qiao <sup>1</sup> Kexin Cao <sup>1</sup> Guozhou
Zheng <sup>1</sup> Xiang Qi <sup>2</sup> Peng Zhang <sup>2</sup> Shumin Deng

## Abstract

Learning from experience is critical for building capable large language model (LLM) agents, yet prevailing
self-evolving paradigms remain inefficient: agents learn in isolation, repeatedly rediscover similar behaviors from
limited experience, resulting in redundant exploration and poor generalization. To address this problem, we propose
SkillX, a fully automated framework for constructing a plug-and-play skill knowledge base that can be reused across
agents and environments. SkillX operates through a fully automated pipeline built on three synergistic innovations: (i)
Multi-Level Skills Design, which distills raw trajectories into three-tiered hierarchy of strategic plans, functional
skills, and atomic skills; (ii) Iterative Skills Refinement, which automatically revises skills based on execution
feedback to continuously improve library quality; and (iii) Exploratory Skills Expansion, which proactively generates
and validates novel skills to expand coverage beyond seed training data. Using a strong backbone agent (GLM-4.6), we
automatically build a reusable skill library and evaluate its transferability on challenging long-horizon,
user-interactive benchmarks, including AppWorld, BFCL-v3, and τ<sup>2</sup>-Bench. Experiments show that SkillKB
consistently improves task success and execution efficiency when plugged into weaker base agents, highlighting the
importance of structured, hierarchical experi ence representations for generalizable agent learning. Our code will be
publicly available soon at https://github.com/zjunlp/SkillX.

## 1. Introduction

Large language model (LLM) based agents (OpenAI, 2025; DeepSeek-AI, 2025; Team et al., 2025b; Yang et al., 2025)

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/7b12c89605093f39cb3c9c20c1a4d57db51995f410c7631bdd76507dd334ab8a.jpg)

Figure 1. Claude Skills follow a long-context, progressively disclosed format, which requires a complex sandboxing
system and multiple interactions, thereby posing challenges to robust reasoning. In contrast, SkillX adopts a
hierarchical, itemized representation that can be stored and retrieved via a lightweight retrieval module and injected
into the system prompt in one time, making it easier to transfer across base models.

have recently demonstrated remarkable progress in longhorizon decision making with tools, enabling complex behaviors
such as API calling (Trivedi et al., 2024; Patil et al., 2025; Li et al., 2025), web navigation (Yao et al., 2023; Zhou
et al., 2024; Mialon et al., 2023), scientific discovery (Ou et al., 2025; Liu et al., 2025; Qiao et al., 2025; Novikov
et al., 2025), and interactive assistants (Barres et al., 2025; Yao et al., 2024; He et al., 2025). Despite these
advances, most agents still approach each new task largely from scratch, relying on direct reasoning or limited
taskspecific demonstrations. This paradigm is costly, brittle, and fundamentally at odds with how intelligent systems
are expected to accumulate and reuse experience over time.

A natural resolution is to enable agents to learn from experience (Sutton, 2025). Recent work has explored selfevolving
agents that iteratively reflect on past executions and improve their behavior over time (Wang et al., 2025c; Fang et
al., 2025c; Zhao et al., 2024; Xu et al., 2025; Cao et al., 2025). While promising, these approaches often fail to
deliver scalable and transferable gains. In practice, experience learning typically suffers from three structural
limitations. (1) Isolated Learning: agents execute the same tasks repeatedly and re-extract similar experiences
independently, leading to substantial redundancy. (2) Weak Generalization of Experience: in complex environments,
high-quality training data are scarce, so the mined experiences often transfer poorly to new tasks. (3) Model Capability
Bottleneck: when experience is harvested solely through an agent’s own exploration and reflection, what can be extracted
is ultimately capped by the agent’s current capability frontier. These challenges point to a more fundamental question:
What form of experience can be broadly reusable across agents of varying capabilities and across diverse environments?

Existing work has proposed multiple representations of experience, such as insights (Cao et al., 2025; Ouyang et al.,
2025), workflows (Wang et al., 2025c;b; Han et al., 2025), or trajectories (Zhao et al., 2024; Fang et al., 2025c).
However, none of these representations simultaneously offer strong transferability, efficient retrieval, and direct
executability. Inspired by Claude Skills (Anthropic, 2025), we argue that skills provide a more suitable abstraction:
they encapsulate reusable competencies that directly support task execution. Nonetheless, prior skill-based designs
often rely on longcontext, progressive disclosure, which place heavy demands on reasoning and environment
instrumentation, limiting robustness and practical reuse, as illustrated in Figure 1.

In this work, we introduce SkillX, a fully automated framework for constructing a plug-and-play skill knowledge base
from agent experience. Our core insight is that transferable experience should be organized hierarchically, rather than
as monolithic behaviors. SkillX therefore represents experience at three complementary levels: (i) Planning Skills,
which capture high-level task organization; (ii) Functional Skills, which implement reusable, toolbased subroutines;
and (iii) Atomic Skills, which encode execution-oriented usage patterns and constraints. This multi-level design yields
skills that are concise, composable, and robust to distributional shifts. SkillX builds such a skill library through a
fully automated pipeline. A strong backbone agent first performs rollouts on training tasks and distills multi-level
skills from successful trajectories. The extracted skills are then iteratively refined through consolidation and
validation, improving library quality over time. Finally, SkillX performs experience-guided exploration to proactively
expand the skill space by targeting under-utilized tools and failure-prone behaviors, enabling generalization beyond the
initial training distribution.

To build a reliable, plug-and-play skill library, we instantiate SkillX with a strong agent backbone, GLM-4.6 (Team et
al., 2025a), and pre-build a skill library on challenging, user-interactive, long-horizon benchmarks, including:
AppWorld (Trivedi et al., 2024), BFCL-v3 (Patil et al., 2025), and $\tau ^ { 2 }$ -Bench (Barres et al., 2025). Our
experiments show that this plug-and-play skill library can be directly plugged into base agents (e.g., Qwen3-32B (Yang
et al., 2025)), yielding around a 10% performance improvement while also improving execution efficiency. We further
demonstrate the advantages of our multi-level skill design for experience representation, and show that both iterative
refinement and skill expansion provide additional gains. In a nutshell, we conclude our contributions as:

• We propose a hierarchical skill representation that transforms raw trajectories into reusable planning, functional,
and atomic skills.

• We present SkillX, a fully automated and extensible framework for pre-building plug-and-play skill libraries for LLM
agents, featuring iterative refinement and skill expansion.

• We release the resulting plug-and-play skill library and provide strong empirical evidence across multiple agent
benchmarks that it can directly enhance the capabilities of weaker agents.

## 2. Preliminaries

Agent Definition We consider a general interactive setting where an agent solves tasks by acting in an environment. An
environment is defined as $\mathcal { E } = ( \mathcal { S } , \mathcal { A } , \mathcal { P } )$ , where A is the set
of executable actions, S is the set of observable states, and $\mathcal { P } ( s ^ { \prime } \mid s , a )$ is the
transition dynamics. At time step t, the agent receives an observation $o _ { t } \in \mathcal { O }$ and produces an
action $a _ { t } \in \mathcal A$ . Following the ReAct style formulation, the agent therefore selects an
action $\hat { a } _ { t } \in \hat { \mathcal { A } }$ conditioned on its
context $c _ { t } = \left( o _ { 1 } , \hat { a } _ { 1 } , \dots , o _ { t - 1 } , \hat { a } _ { t - 1 } , o _ { t } \right)$

$$
\hat {a} _ {t} \sim \pi (\cdot \mid c _ {t}), \qquad \hat {a} _ {t} \in \hat {\mathcal {A}}.\tag{1}
$$

Executing $\hat { a } _ { t } \in \mathcal A$ yields a new observation via the environment. The final trajectory
is $\tau = ( o _ { 1 } , \hat { a } _ { 1 } , \dots , o _ { T } , \hat { a } _ { T } )$

LLM Agent and Skill-Conditioned Execution. Let Q be the tasks set. We write $q \in \mathcal { Q }$ for sampling a task,
and let $R ( \tau , q ) \in \{ 0 , 1 \}$ be a task-dependent success indicator. We model the LLM agent as a policy π
that induces a trajectory distribution. Without external skills, the agent generates trajectories by direct reasoning:

$$
\tau \sim \pi (\cdot | q), \quad q \in \mathcal {Q}.\tag{2}
$$

To reduce redundant exploration and improve task completion, we equip the agent with a skills
library $\mathcal { D } =$ $\{ s _ { 1 } , \dotsc , s _ { | \mathcal { D } | } \}$ and a skill retriever that recalls a
set of relevant skills for the current task. Concretely, given $q \in \mathcal { Q } .$ a retrieval function (typically
implemented via semanticsimilarity retrieval) $\rho : \mathcal { Q } 2 ^ { \mathcal { D } }$ . returns a skill
subset $S _ { q } \ = \ \rho ( q ) , S _ { q } \ \subseteq \mathcal { D }$ . The LLM agent then generates a trajectory
by conditioning on the retrieved skill set:

$$
\tau^ {\prime} \sim \pi (\cdot | \mathcal {S} _ {q}, q), \quad q \in \mathcal {Q}.\tag{3}
$$

Our objective is to design the skills library D and the usage within π such that the expected success rate is improved:

$$
\mathbb {E} _ {q \in \mathcal {Q}, \tau^ {\prime} \sim \pi (\cdot | \mathcal {S} _ {q}, q)} R (\tau^ {\prime}, q) > \mathbb {E} _ {q \in \mathcal {Q}, \tau \sim \pi (\cdot | q)} R (\tau , q).\tag{4}
$$

## 3. SkillX Design and Implementation

## 3.1. Multi-Level Skills Design

In tool-centric agent scenarios, we structure the skills required by the model into three levels (see Figure 2):

$$
\mathcal {D} = S _ {\text { plan }} \oplus S _ {\text { func }} \oplus S _ {\text { atomic }},\tag{5}
$$

corresponding to planning skills, functional skills, and atomic skills, respectively. In a given
environment $\mathcal { E } ,$ let $\tau$ denote the set of tool actions. (i) Atomic
skill $s _ { \mathrm { a t o m i c } }$ is aligned with a single tool $t \in \mathcal { T }$ and is modeled as an
extended semantic specification of t, e.g., as enriched descriptions, constraints, or usage patterns that refine the
effective behavior of t. (ii) Functional skill $s _ { \mathrm { f u n c } }$ abstracts a subtask and can be regarded as
a macro-operation that accomplishes a sub-query. We assume each task q admits a decomposition into n
subtasks, $\{ q _ { \mathrm { s u b t a s k , 1 } } , q _ { \mathrm { s u b t a s k , 2 } } , \hdots , q _ { \mathrm { s u b t a s k } , n } \}$
and each $s _ { \mathrm { f u n c } }$ corresponds to skills to accomplish $q _ { \mathrm { s u b t a s k } , i }$ .
Specifically, $s _ { \mathrm { f u n c } }$ is grounded in a set of tool actions, which can be instantiated as a
composition of tools ${ \mathcal { T } } _ { \mathrm { f u n c } } \subseteq { \mathcal { T } }$ . (iii) planning
skill $s _ { \mathrm { p l a n } }$ aligns with the organizational structure of the subtasks (e.g., ordering,
dependencies, and branching), specifying how functional skills should be composed to solve $q .$ Next, we describe the
extraction methods for the three skill levels.

## 3.2. Rollout and Skills Extraction

Given a task $q ,$ we first perform m-sized rollouts, reusing the agent’s inference procedure to collect trajectories.
We then extract the multi-level skills from these trajectories, with skill extractor $f .$ Details of the inference
procedure are provided in Section 4.

Planning Skills Extraction. Given a successful trajectory, we extract the planning skill $s _ { \mathrm { p l a n } }$
by compressing the trajectory into an ordered set of high-level steps. During this compression, we explicitly filter out
non-essential transitions such as exploration, backtracking, and trial-and-error behaviors that are incidental to the
final solution but detrimental to skill reuse. Moreover, for excessively long or verbose environment feedback, we apply
summarization to obtain compact state descriptions, which improves the stability and fidelity of the extracted
high-level skills.

Functional Skills Extraction. We leverage the previously extracted planning skill $s _ { \mathrm { p l a n } }$ to guide
the extraction of functional skills. Concretely, given a plan and its corresponding trajectory, we iteratively prompt
the model to extract the functional skill $s _ { \mathrm { f u n c } }$ that aligns with the objective of each
subtask $q _ { \mathrm { s u b t a s k } , i }$ . Formally, each $s _ { \mathrm { f u n c } }$ is represented with three
key fields: name (the skill name), document (a description of inputs, outputs and usage notes), and content (the tool
invocation pattern for completing subtask $q _ { \mathrm { s u b t a s k } , i } )$

Atomic Skills Extraction. Atomic skills are single tool specifications that extend the original tool schema with
reusable, execution-oriented usage patterns. They serve as a low-level complement when higher-level functional
skills $s _ { \mathrm { f u n c } }$ are missing or incomplete. We prompt the model to
distill $s _ { \mathrm { a t o m i c } }$ from trajectories the invocation patterns, typical parameter configurations,
and practical notes, especially constraints and common failure modes observed in real usage. The representation
of $s _ { \mathrm { a t o m i c } }$ is unified with $s _ { \mathrm { f u n c } }$

## 3.3. Iterative Skills Refinement

With only a limited amount of seed training data, a key question is whether we can maximize the utility of the available
supervision to extract additional skills and continuously improve existing ones. Inspired by prior works (Cai et al.,
2025b;a; Yuksekgonul et al., 2024), we adopt a text-based iterative optimization paradigm for the skill library.
Concretely, at k-th iteration, we start from the current skill library $\mathcal { D } ^ { ( k ) }$ , repeatedly
rollouts from the training set, then extract multi-level skills. We subsequently apply a refinement operator $\phi ,$
including: Skills Merge and Skills Filter. Finally, we update the skill library $\mathcal { D } ^ { ( k ) }$ with the
refined skills to obtain skill library $\mathcal { D } ^ { ( k + 1 ) }$ , including three update operations: add, modify
or keep.

Iterative Skills Library Construction. We construct the skill library in an iterative manner.
Let $ { \mathcal { D } ^ { ( 0 ) } } = \emptyset$ be an initial empty library. In iteration $k = 0 , 1 , \ldots ,$ we
roll out the agent augmented with the current library $\mathcal { D } ^ { ( k ) }$ on tasks sampled from the training
set $\mathcal { Q } _ { \mathrm { t r a i n } }$ to obtain a set of trajectories

$$
\tau^ {(k)} \sim \pi (\cdot | \rho_ {\mathcal {D} ^ {(k)}} (q), q), \quad q \in \mathcal {Q} _ {\text {train}},\tag{6}
$$

and denote $\mathcal { K } ^ { ( k ) } = \{ \tau _ { 1 } ^ { ( k ) } , . . . , \tau _ { N _ { k } } ^ { ( k ) } \}$ . A
skill extractor $f$ produces a variable-size set of candidate skills from each
trajectory, $S _ { i } ^ { ( k ) } = f ( \tau _ { i } ^ { ( k ) } )$ and we aggregate all the skills extracted from the
batch
via $\begin{array} { r } { S ^ { ( k ) } = \bigcup _ { i = 1 } ^ { N _ { k } } S _ { i } ^ { ( k ) } } \end{array}$ .
Additionally, we define a refinement operator $\phi$ to merge and filter the skills. The library is then updated as

$$
\mathcal {D} ^ {(k + 1)} \triangleq \mathcal {D} ^ {(k)} \cup \phi (\mathcal {S} ^ {(k)}) = \mathcal {D} ^ {(k)} \cup \phi \left(\bigcup_ {i = 1} ^ {N _ {k}} \mathcal {S} _ {i} ^ {(k)}\right).\tag{7}
$$

Let $\mathcal { Q } _ { \mathrm { t e s t } }$ denote a test distribution. We aim to iteratively improve the library
such that the performance of the induced skill-conditioned agent is maximized
on $\mathcal { Q } _ { \mathrm { t e s t } }$

$$
\max _ {k} \mathbb {E} _ {q \sim \mathcal {Q} _ {\mathrm{test}}} \left[ \mathbb {E} _ {\tau \sim \pi (\cdot | \rho_ {\mathcal {D} (k)} (q), q)} [ R (\tau , q) ] \right],\tag{8}
$$

and we stop the iteration when this test performance no longer improves.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/eefd188023c6470340e492be2dd8047e37688e25a7240a7b22b4ac0ddd4dca75.jpg)

Figure 2. SkillX provides an automated, iterative pipeline for constructing a skills library, integrating skills
extraction. skills expansion and skills refinement. The skills library is organized into three levels: planning skills,
functional skills, and atomic skills.

Skills Merge. After extracting skills from each trajectory, we often obtain many functionally redundant skills that,
despite surface differences, correspond to the same underlying skill pattern. How to update a single skill when multiple
heterogeneous update directions are available? We merge skills from an optimization-based perspective. For a specific
skill s with current embedding, we first retrieve and cluster a set of semantically similar skills using cosine
similarity. The resulting cluster can be interpreted as providing multiple complementary update directions for the same
underlying skill, a multi-dimensional refinement of s. Let $\mathcal { Z } ( s ) = \{ 1 , \ldots , z \}$ index the
semantically similar skills associated with skill s. Each neighbor i induces a candidate update
direction $\delta _ { i } .$ , yielding a candidate updated state

$$
s _ {i} ^ {\prime} = s + \delta_ {i}, \quad i \in \mathcal {Z} (s).\tag{9}
$$

We then aggregate these candidate directions into the final direction. The simplest form is to merge the
directions: $\begin{array} { r } { \delta _ { \mathrm { a g g } } ~ = ~ \sum _ { i \in \mathcal { Z } ( s ) } \delta _ { i } } \end{array}$ .
The final update is applied as

$$
s ^ {+} = s + \delta_ {\mathrm{agg}}.\tag{10}
$$

Specifically, we treat the semantically similar skills as multiple update views of the same skill, and we use the
combined direction as the final update direction. Finally, we merge semantically similar skills into a single skill. If
the merged skill becomes overly complex, we further decompose it into more modular, reusable skills.

Skills Filter. We enforce skill quality via a strict two-stage filtering procedure. (1) General Filter. This stage
removes skills that are unlikely to be portable or compositional, including those that depend on extraneous Python
packages, expose overly idiosyncratic function-style definitions, or overly-encapsulated skills. (2) Tool-specific
Filter. This stage mitigates tool-use hallucinations by validating each skill against the environment-provided tool
schema, rejecting skills that reference non-existent tools, invalid parameters, or schema-incompatible argument
structures. Together, these filters maintain a high-precision skill library while preserving flexibility across
heterogeneous agent benchmarks.

Skills Library Update. After completing Skill Merge and Skill Filter, we perform concrete updates to the skill
library $\mathcal { D } ^ { k }$ for the k-th iteration, including three types: add new skills, modify existing skills,
and keep skills unchanged. Furthermore, the entire pipeline can be executed iteratively over multiple rounds. Through
this continual update process, the skill library progressively improves in coverage, quality, and compositional
richness, enabling increasingly effective skill reuse for downstream agent tasks.

## 3.4. Exploratory Skills Expansion

While skills distilled from a seed training set $\mathcal { Q } _ { \mathrm { t r a i n } }$ can already improve an
agent’s performance, relying solely on scarce demonstrations is insufficient in complex environments with large tool
spaces (e.g., (Trivedi et al., 2024) exposes hundreds of APIs). Inspired by Zhai et al. (2025), we adopt an Experience
Guiding Exploration scheme to broaden coverage beyond what is observed in the seed data, encouraging the agent to
interact with the environment and exercise a wider range of tools. We guide exploration using experience collected from
rollouts on the seed set (e.g., tools the agent already uses reliably, tools with high failure rates, and tools that are
never invoked), thereby prioritizing underexplored or failure-prone tools to improve sample efficiency. After collecting
exploratory trajectories, we synthesize new tasks ${ \mathcal { Q } } _ { \mathrm { s y n } }$ from these interactions,
and then rerun our skill acquisition and refinement pipeline on the resulting data to iteratively expand the skill
library. Compared to the random exploration strategy (Zhai et al., 2025), our approach discovers a more diverse set of
skills.

## 4. SkillX Usage

Planning Skills Retrieval and Pseudo-Plan Rewriting. For a novel and complex agent task q, directly retrieving past
experiences based solely on task similarity may lead to a mismatch between retrieved experiences and the actual
execution trajectory. This issue becomes particularly pronounced in environments where execution dynamics are strongly
influenced by user profiles, contextual constraints, or other external factors. To improve retrieval relevance, inspired
by (Gao et al., 2022), we first retrieve high-level planning skills associated with similar
tasks $\mathcal { P } ( q ) = \rho ( q )$ where ρ is a similarity retrieval function and $\mathcal { P } ( q )$ is the
retrieved planning skills. Then we prompt the model to self-rewrite a task-specific pseudo-plan conditioned on the
current task $\tilde { p } ( q ) = \mathrm { L L M } _ { \mathrm { r e w r i t e } } ( q , \mathcal { P } ( q ) )$ .
This rewritten pseudo-plan serves as an intermediate retrieval query to better align subsequent skill retrieval with the
current execution setting. To mitigate hallucination risks and prevent speculative content from affecting agent
behavior, the pseudo-plan is not injected into the final system prompt.

Functional and Atomic Skills Retrieve. Given the rewritten
pseudo-plan $\tilde { p } ( q ) = \{ \mathrm { s t e p } _ { 1 } , \mathrm { s t e p } _ { 2 } , \ldots , \mathrm { s t e p } _ { p } \}$ ,
we treat each step as a retrieval query to retrieve functional and atomic skills.
For ${ \mathrm { s t e p } } _ { i } ,$ we first retrieve relevant
skills $S _ { i } ~ = ~ \rho ( \mathrm { s t e p } _ { i } )$ and then remove duplicates across
steps, $S ^ { \prime } ~ = ~ \mathrm { d e d u p } \Big ( \bigcup _ { i = 1 } ^ { p } S _ { i } \Big )$ . To keep the
context concise and task-relevant, we further ask the LLM to self-filter the retrieved candidates and retain only
applicable skills $ { S _ { q } } = \mathrm { L L M }$ select $( q , \tilde { p } ( q ) , S ^ { \prime } )$ ,
where $\textstyle { \mathcal { S } } _ { q }$ is the final skill set used for solving the query q.

## 5. Experiment

## 5.1. Experimental Settings

Benchmarks and Metrics. We conduct the evaluation on complex, long-horizon, user-interactive agent benchmarks,
including $\mathrm { \ B F C L - v 3 }$ (Patil et al., 2025), AppWorld (Trivedi et al., 2024), and $\tau ^ { 2 }$
-bench (Barres et al., 2025). For BFCL-$\mathbf { v } 3 ,$ , we use the base multi-turn category and randomly split it
into 50 training instances and 150 test instances. AppWorld provides 90 training instances and the Test Normal category
as test set. $\tau ^ { 2 } .$ -bench defines training and test splits for each sub-domain. Additional details are
provided in the Appendix A.1. For AppWorld and BFCL-v3, we report Avg@4 and Pass@4, the average success rate over four
independent runs and the probability of succeeding at least once across four runs, respectively. Following the (Barres
et al., 2025) evaluation setup, we report Passˆ1, the pass rate over running four times.

Models and Baselines. To assess the effectiveness of SkillX, we evaluate three Agentic base models that vary in model
size and reasoning style (thinking and nonthinking), including Qwen3-32B (Yang et al., 2025), Kimi-K2-Instruct-0905 (
Team et al., 2025b), and GLM-4.6 (Team et al., 2025a). Among them, GLM-4.6 has been reported to exhibit strong native
agentic capabilities in agent midtraining, serving as a competitive backbone for our study.

We compare against four representative baselines: (1) Nomemory, which performs inference without retrieving any prior
experience; (2) A-Mem (Xu et al., 2025), a system that dynamically manages structured episodic memories; (3) AWM (Wang
et al., 2025c), which reuses modular workflows distilled from historical trajectories; and (4) ExpeL (Zhao et al.,
2024), which retrieves relevant past trajectories as few-shot demonstrations and incorporates distilled insights to
improve LLM performance. For a fair comparison, all methods retrieve experience only based on the user’s initial query
and insert the retrieved content into the system prompt following a unified protocol. Full baseline details are provided
in the Appendix A.2.

Implementation Details. To construct SkillX, we use GLM-4.6 (Team et al., 2025a) independently rollouts four times per
training task, followed by skill extraction, skill refinement, and skill expansion. The maximum number of refinement
iterations is set to 3. For efficiency, we limit environment exploration to one rollout per training task; the sampling
temperature is 1.0 during exploration. We use Qwen3-Embedding-8B (Zhang et al., 2025d) for both skill deduplication and
skill retrieval, with a minimum cosine similarity threshold of 0.45 for retrieval. During solving new tasks, we use the
same model for both Pseudo-Plan rewriting and action execution. For the other baselines, we evaluate two settings: (1)
Distillation paradigm: a strong agent (GLM-4.6) is used to extract experiences to build an experience repository, and
the execution model then performs inference; (2) Self-evolution paradigm: the experience extraction model is kept
consistent to the execution model to enable self-extraction, following the original experimenta protocol of each method.
Additional implementation details are provided in the Appendix A.3.

SkillX: Automatically Constructing Skill Knowledge Bases for Agents


<table><tr><td rowspan="2">Model</td><td rowspan="2">Methods</td><td colspan="2">BFCL-V3</td><td colspan="2">AppWorld</td><td colspan="3"><eq>\tau^2</eq>-Bench</td></tr><tr><td>Avg@4</td><td>Pass@4</td><td>Avg@4</td><td>Pass@4</td><td>Retail</td><td>Airline</td><td>Telecom</td></tr><tr><td rowspan="7">Qwen3-32B</td><td>No Memory*</td><td>53.67</td><td>73.33</td><td>27.68</td><td>47.62</td><td>53.75</td><td>38.75</td><td>36.25</td></tr><tr><td>A-Mem*</td><td>53.67</td><td>73.00</td><td>26.79</td><td>50.59</td><td>53.12</td><td>38.75</td><td>38.12</td></tr><tr><td>AWM*</td><td>55.67</td><td>76.00</td><td>30.80</td><td>55.95</td><td>55.00</td><td>40.00</td><td>38.12</td></tr><tr><td>AWM‡</td><td>56.67</td><td>76.33</td><td>34.45</td><td>56.25</td><td>57.50</td><td>41.25</td><td>40.62</td></tr><tr><td>ExpeL*</td><td>57.33</td><td>77.67</td><td>32.87</td><td>58.93</td><td>56.25</td><td>42.50</td><td>39.38</td></tr><tr><td>ExpeL‡</td><td>59.33</td><td>78.83</td><td>32.94</td><td>58.78</td><td>58.12</td><td>43.75</td><td>41.25</td></tr><tr><td>SkillX‡</td><td>63.67</td><td>82.00</td><td>35.12</td><td>58.93</td><td>66.87</td><td>47.50</td><td>43.75</td></tr><tr><td rowspan="7">Kimi-K2-Instruct-0905</td><td>No Memory*</td><td>65.17</td><td>78.00</td><td>46.88</td><td>70.24</td><td>75.62</td><td>51.25</td><td>78.12</td></tr><tr><td>A-Mem*</td><td>65.17</td><td>76.67</td><td>46.58</td><td>72.62</td><td>76.25</td><td>52.50</td><td>76.87</td></tr><tr><td>AWM*</td><td>65.33</td><td>79.00</td><td>49.70</td><td>76.19</td><td>76.25</td><td>53.75</td><td>77.50</td></tr><tr><td>AWM‡</td><td>64.67</td><td>79.17</td><td>50.60</td><td>76.49</td><td>76.25</td><td>53.75</td><td>77.50</td></tr><tr><td>ExpeL*</td><td>66.33</td><td>79.33</td><td>52.53</td><td>78.57</td><td>77.50</td><td>55.50</td><td>78.75</td></tr><tr><td>ExpeL‡</td><td>66.00</td><td>79.67</td><td>52.98</td><td>78.87</td><td>77.50</td><td>56.25</td><td>79.37</td></tr><tr><td>SkillX‡</td><td>66.83</td><td>81.33</td><td>56.40</td><td>81.55</td><td>78.12</td><td>58.75</td><td>82.50</td></tr><tr><td rowspan="5">GLM-4.6</td><td>No Memory*</td><td>76.67</td><td>83.33</td><td>60.27</td><td>83.33</td><td>76.25</td><td>70.00</td><td>70.63</td></tr><tr><td>A-Mem*</td><td>76.50</td><td>83.00</td><td>60.57</td><td>83.93</td><td>76.88</td><td>70.00</td><td>68.75</td></tr><tr><td>AWM*</td><td>77.17</td><td>84.00</td><td>62.20</td><td>84.52</td><td>77.50</td><td>71.25</td><td>70.63</td></tr><tr><td>ExpeL*</td><td>78.83</td><td>85.33</td><td>64.14</td><td>85.12</td><td>77.50</td><td>72.50</td><td>71.25</td></tr><tr><td>SkillX*</td><td>79.50</td><td>86.00</td><td>64.88</td><td>88.69</td><td>82.50</td><td>76.25</td><td>71.88</td></tr></table>


Table 1. Main results of SkillX on three benchmarks. Methods with ∗ mean that the experience extraction model is aligned
with the inference model. Methods with ‡ mean that GLM-4.6 is used for experience extraction, while inference still
relies on the original model.

## 5.2. Main Results

SkillX Boost Agentic Performance of Base LLMs. As shown in Table 1, SkillX improves the base model’s performance. In
particular, Qwen3-32B gains roughly around 10 points across multiple benchmarks. For K2 (Kimi-K2- Instruct-0905), we
observe a clear improvement on App-World, whereas the gains are modest on the other two tool call intensive benchmarks.
We infer this is because K2 relies more heavily on the original tool schema and does not effectively leverage the
additional contextual information.

Multi-Level Skills Design Outperform Other Forms of Experience Representation. When the experience extraction model is
aligned with the execution model, SkillX consistently outperforms all baseline methods, as indicated by the methods
with ∗ in Table 1. Among them, ExpeL retrieves past trajectories and uses them as few-shot demonstrations, which
provides a more direct performance gain than the other baselines. However, the agent capabil-

ity required for multi-level skill decoupling offers a more advantageous form of experience representation.

Suboptimal Experience Representations Hinder Transfer Performance. We further evaluate the GLM-4.6 extracted experience
with AWM and ExpeL on the weaker models, see the results of methods with ‡ in Table 1. However, the performance still
lagged behind that of SkillX. This indicates that distilling experience from a strong model is effective, but the form
of experience representation is even more critical. Consequently, suboptimal experience representation can hinder
effective experience transfer. These results further demonstrate the advantage of SkillX in transferring experience
across base models.

SkillX can Expand Base Model’s Capability Boundary. We observe that experience-based learning leads to substantial
Pass@4 improvements for the weaker models, K2 and Qwen3-32B. This suggests that, in practice, the most direct way to
extend the capability boundary of a base model is to distill knowledge from a stronger model (Yue et al., 2025). In
contrast, for the stronger model GLM-4.6, neither the baseline nor SkillX yields a significant gain in Pass@4. This
indicates that stronger models already possess robust capabilities in exploration, planning, and tool use, leaving
limited headroom for further capability expansion via experience-based augmentation. Nevertheless, the modest
improvements still support the effectiveness of SkillX.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/a91559cc978ebc0101f1e3b07a74997a4b2e9376c277d5dbef5dcfe8a2105ecf.jpg)

(a) Performance of Multi-Level Skills

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/def78d7b0e6bf13bbb9964d8c2a9338df10c8528c1d1e69dadac84c05e43da34.jpg)

(b) Execution Efficiency of Multi-Level Skills

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/a421aefb2f0b71f62795f76c2603f859bf5394a0c54458ce24e6e306ef29814c.jpg)

(c) Analysis of Iterative Refinement

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/629f681096c3441fdaa351912d84fb99cab556d080edf7314dd458a1827576b1.jpg)

(d) Skill Expansion Strategies Analysis

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/0002d0f29ee9e514c10de25aeb364d5229c40cf4fc6e7b68b121b65292c560f7.jpg)

(e) Average Execution Steps

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/696ceb2908cf93bfce164727541a75025beeb98070b97a55fc2a93a2dd0b66d2.jpg)

(f) Average Input Tokens

Figure 3. Comprehensive Analysis of SkillX. (a) Performance of Multi-skills: Models exhibit varying performance under
different skill composition. (b) Execution efficiency of Multi-skills: Jointly composing all skills yields the best
execution efficiency. (c) Iterative optimization: Iterative skill refinement further improves performance. (d) Skill
expansion strategies: Experience-guided expansion achieves the best on scalability and performance gains. (e) Analysis
of Input tokens: Properly balancing input tokens is crucial fo controlling inference cost. (f) Analysis of Execution
steps: Experience-based learning reduces the number of execution steps.

## 5.3. Analysis

Which skill is more effective? We analyze the behaviors of our multi-level skill across models on AppWorld, and the
results are shown in Figure 3 (a) and Figure 3 (b). (i) Planning skills consistently reduce the number of execution
steps across all models, with particularly pronounced gains for weaker models such as Qwen3-32B and K2, especially when
combined with Functional Skills. We attribute this to their limited exploration capability in complex environments.
Notably, for Qwen3-32B, adding Functional and Atomic Skills can even hurt performance, as the model tends to
over-imitate retrieved skills rather than adapt them to novel tasks. For stronger models, pseudo-planning may fail to
faithfully capture underlying environment dynamics in complex scenarios, and can therefore become counterproductive. (
ii) Functional skills contribute the most to overall performance improvements: equipping K2 and GLM-4.6 with Functional
and Atomic Skills alone already yields observable gains, highlighting the advantage of skills as an effective
representation of experience. (iii) Atomic skills provide crucial clarifications for key APIs. When they are absent,
performance drops substantially, further validating the need to supplement tool schemas and to cover tools missing from
Functional Skills. Finally, we find that GLM-4.6 benefits the most from using all skill types; K2 performs best with
Functional + Atomic Skills; and Qwen3-32B achieves its best performance when only Planning Skills are enabled. This
further demonstrates that multi-level skills can comprehensively cover the capabilities required for diverse models to
execute agent tasks.

Iterative Refinement Strategies Further Enhances SkillX Performance. We evaluate effectiveness of multi-round iterative
refinement for the skill library of SkillX on AppWorld (Figure 3 (c)). Overall, multiple iterations further improve
performance on both training and test sets. Leveraging existing training data, the process continually improves various
aspects of skills, such as documentation and content. Besides, it can slightly expand the size of the skill library (
Figure 3 (d)). However, when training data are limited, text-only optimization can lead to overfitting. Thus, selecting
an appropriate number of update rounds is crucial to obtain a higher-quality skill library.

Skill Expansion Strategies Improve Generalization. We compare two skill expansion strategies: random exploration and
experience-guided expansion. The results are as shown in Figure 3 (d). In terms of skill growth, the experience-guided
strategy yields substantially more novel skills, as random exploration treats past executions in isolation and
repeatedly rediscovers already identified skills. Empirically, the experience guided strategy yields performance
improvement through skill expansion. Overall, our results indicate that in complex environments, particularly under
scarce training data, skill expansion is a crucial component of experience learning.

SkillX Enhances Agent Execution Efficiency. Learning from experience not only improves the performance of the base
model, but also enhances the execution efficiency of the agent. Our experiments further corroborate this effect (see
Figure 3 (e) and Figure 3 (f)). Although we do not achieve the minimum number of execution steps or the fewest input
tokens, we obtain the best overall performance (see Table 1). These results further highlight the advantages of our
multi-level skill design and skills library construction.

## 6. Further Analysis

## 6.1. Evaluating SkillX Across Other Base Models

We further evaluate SkillX on stronger base models, including DeepSeek-V3.2 and GPT-4.1, which are at least comparable
to, and in some cases stronger than GLM-4.6. We find that SkillX provides consistent performance gains, whether the
skills are extracted by these stronger models themselves or constructed using GLM-4.6.

<table><tr><td rowspan="2">Methods</td><td colspan="2">BFCL-v3</td><td colspan="2">Appworld</td></tr><tr><td>Avg@4</td><td>Pass@4</td><td>Avg@4</td><td>Pass@4</td></tr><tr><td colspan="5">DeepSeek-V3.2</td></tr><tr><td>No Memory</td><td>64.33</td><td>81.33</td><td>61.90</td><td>84.08</td></tr><tr><td>SkillX</td><td></td><td></td><td></td><td></td></tr><tr><td>GLM-Extract</td><td>67.17</td><td>83.33</td><td>64.28</td><td>86.90</td></tr><tr><td>Self-Extract</td><td>67.83</td><td>84.67</td><td>65.48</td><td>88.39</td></tr><tr><td colspan="5">GPT-4.1</td></tr><tr><td>No Memory</td><td>49.66</td><td>58.39</td><td>66.37</td><td>82.74</td></tr><tr><td>SkillX</td><td></td><td></td><td></td><td></td></tr><tr><td>GLM-Extract</td><td>60.00</td><td>69.33</td><td>66.82</td><td>84.52</td></tr><tr><td>Self-Extract</td><td>50.67</td><td>56.67</td><td>68.60</td><td>82.14</td></tr></table>


Table 2. Performance of SkillX on other base models.

## 6.2. Ablation Study on Three Components of SkillX

We conduct ablation studies on the three key components of SkillX, i.e., multi-level skills design, skills refinement,
and skills expansion, as shown in Table 3. The results in Table 3 suggest that SkillX is robust to its underlying
experience representation, while iterative refinement and skill expansion can offer further improvements depending on
the model and the particular combination of components.

Please note that we do not perform ablations of skills iteration and skills expansion on τ <sup>2</sup>-Bench. This is
because $\tau ^ { 2 } \mathrm { . }$ Bench is a user-interactive benchmark whose tool schemas are relatively simple in
both number and dependency structure, and its training set already covers many task patterns directly. More broadly, for
user-centric benchmarks of this type (e.g., dialogue benchmarks), it remains an open question whether experience
learning centered around toolschema-based skills is the most appropriate formulation. Therefore, we believe that
component studies on skill iteration and skill expansion are less suitable for $\tau ^ { 2 } .$ -Bench, and we do not
include them in our ablation experiments.

<table><tr><td rowspan="2">Model</td><td rowspan="2">Methods</td><td colspan="2">BFCL-V3</td><td colspan="2">AppWorld</td></tr><tr><td>Avg@4</td><td>Pass@4</td><td>Avg@4</td><td>Pass@4</td></tr><tr><td rowspan="7">GLM-4.6</td><td>No Memory</td><td>76.67</td><td>83.33</td><td>60.27</td><td>83.33</td></tr><tr><td>Vanilla-Iter1</td><td>78.50</td><td>85.33</td><td>62.35</td><td>83.33</td></tr><tr><td>Vanilla-Iter2</td><td>79.50</td><td>86.00</td><td>64.29</td><td>85.12</td></tr><tr><td>Vanilla-Iter3</td><td>78.83</td><td>84.67</td><td>61.46</td><td>85.71</td></tr><tr><td>Expand-Iter1</td><td>78.50</td><td>85.33</td><td>64.58</td><td>83.93</td></tr><tr><td>Expand-Iter2</td><td>78.83</td><td>85.33</td><td>64.88</td><td>87.50</td></tr><tr><td>Expand-Iter3</td><td>78.83</td><td>84.67</td><td>64.88</td><td>88.69</td></tr></table>


Table 3. Ablation results of SkillX on three components. Specifically, Vanilla-Iter1 uses only the multi-level skills
design; Vanilla-Iter2 and Vanilla-Iter3 additionally incorporate skills refinement; Expand-Iter1 uses the multi-level
skills design together with skills expansion; Expand-Iter2 and Expand-Iter3 combine multi-level skills design, skills
refinement, and skills expansion.

## 6.3. Case Study

We also provide qualitative cases to illustrate how agents leverage SkillX and how retrieved skills shape their behavior
when solving unseen tasks. Detailed cases are presented in Appendix B. These cases show that skill libraries help agents
avoid common failures such as incorrect API call sequences, missing prerequisite checks, and the inability to handle
conversational topic shifts. By framing domain knowledge as reusable skills, agents can complete complex multi-step
tasks that the baseline method fails, reducing trial and error from multiple failed attempts to successful execution on
the first attempt.

## 7. Related Work

Encoding For Agent Experience. With the advent of the experience era (Sutton, 2025), agents can achieve selfevolving (
Gao et al., 2025; Fang et al., 2025b; Xia et al., 2026) by encoding past experience and reusing it in context (Dou et
al., 2026) to guide future behavior. Existing approaches to text token-level experience encoding (Zhang et al., 2025b;
Hu et al., 2025) can be broadly grouped into three categories: (i) Case-based Experience: Agents directly store
successful task-execution trajectories and retrieve them later as few-shot examples to new problem solving (Zhao et al.,
2024; Zheng et al., 2024; Zhou et al., 2025). (ii) Strategy-based Experience: By summarizing and contrasting successful
versus failed trajectories, agents distill higherlevel insights or workflows (Cao et al., 2025; Ouyang et al., 2025; Cai
et al., 2025a; Wang et al., 2025c; Tang et al., 2025; Zhang et al., 2025a). (iii) Skill-based Experience: Trajectories
are segmented and distilled into modular, reusable skills, such as textual skills or programmatic skills (Wang et al.,
2025b;a; 2024; Fang et al., 2025c; Han et al., 2025;

Chen et al., 2026; Zheng et al., 2026; Wang et al., 2026a; Zhou et al., 2026a; Zhang et al., 2026b; Ni et al., 2026;
Zhou et al., 2026b). However, it remains unclear which unified experience representation is both easily pluggable and
consistently effective, especially in diverse and complex agentic tool-use scenarios (Trivedi et al., 2024; Yao et al.,
2024; Patil et al., 2025; Barres et al., 2025; He et al., 2025; Li et al., 2025; Zheng et al., 2025; Jiang et al., 2026;
Xing et al., 2026; Li, 2026; Li et al., 2026). In this work, we adopt a hybrid representation, high-level planning
coupled with textual skills, which yields substantial improvements for the base model.

Agent Experience Knowledge Base Construction. The construction pipeline of an experience knowledge base typically
consists of two steps: static construction and dynamic updating. (i) Static construction repeatedly attempts tasks on a
training set or human-curated information sources, extracts experience, and iteratively refines it until performance
plateaus (Zhang et al., 2025c; Cai et al., 2025b; Anthropic, 2025; Wang et al., 2026b; Gallego, 2026; Yang et al.,
2026a). (ii) Dynamic updating updates the ExperienceKB immediately after executing new tasks, enabling experience reuse
in subsequent tasks (Latimer et al., 2025; Fang et al., 2025a; Cao et al., 2025; Du et al., 2025; Yang et al., 2026b;
Yao et al., 2025; Zhang et al., 2026a; Liang et al., 2026).

While dynamic updating is central to continual learning from experience, pre-building a strong static ExperienceKB
remains necessary in practice. However, under the taskscarcity challenge in complex agent settings (Patil et al., 2025;
Barres et al., 2025; He et al., 2025; Li et al., 2025), we further extend skills by combining task synthesis (Zha et
al., 2025; Mai et al., 2025; Shi et al., 2025; Ramrakhya et al., 2025; Guo et al., 2025) to construct more challenging
tasks. To our knowledge, this is the first work to provide a directly reusable skill knowledge base together with an
automated pipeline for skill construction.

## 8. Conclusion

We introduced SkillX, an automated framework for building a plug-and-play skill library for LLM-based agents. To enable
more efficient experience transfer, we design a multi-level skills, including planning skills, functional skills, and
atomic skills from the perspective of tool granularity. SkillX iteratively refines and expands the library through three
core components: i) skills extraction, which rolls out an agent with the current library and extracts multi-level
skills; ii) skills refinement, which iteratively improves skills using execution feedback, while maintaining quality via
skill merging and strict filtering; and iii) exploratory skills expansion, which proactively broadens coverage beyond
the seed training set. Our experiments demonstrate that SkillX transfers effectively to other models and provides
advantages in experience representation. Finally, we will release the optimized skill library constructed by SkillX to
facilitate further community exploration.

## Impact Statements

This work advances generalizable agent learning by transforming isolated trial-and-error experience into a reusable,
structured skill knowledge base that can be shared across agents and environments. By enabling weaker agents to benefit
from skills distilled by stronger ones, the proposed framework reduces redundant exploration, improves sample
efficiency, and lowers the computational and environmental costs of training LLM agents. The plug-and-play design
promotes modularity and reproducibility, supporting broader adoption in long-horizon, user-interactive applications.
Potential risks include over-reliance on pre-built skills and the propagation of biases present in source agents;
however, the automated refinement and expansion mechanisms provide a pathway to mitigate stagnation and encourage
continual adaptation.

## Limitations

Cross-environment transfer. SkillX is currently most naturally applicable when skills can be grounded in a relatively
stable tool environment. The extracted skills are associated with specific tool schemas, which makes direct reuse across
substantially different domains or tool ecosystems less straightforward.

User-interactive settings. The current study focuses mainly on tool-using agent environments. More user interactive
scenarios, particularly dialogue scenarios without function calls, are not yet the primary focus of this work.

## Acknowledgement

This work was supported by the Yongjiang Talent Introduction Programme (2021A-156-G), the Ant Group through CCF-Ant
Research Fund (CCF-AFSG RF20250515), and Information Technology Center and State Key Lab of CAD&CG, Zhejiang University.
This work was supported by Ant Group and Zhejiang University - Ant Group Joint Laboratory of Knowledge Graph.

## References

Anthropic. skills. https://github.com/anthropics/skills, 2025. URL https://github.com/anthropics/ skills. GitHub
repository.

Barres, V., Dong, H., Ray, S., Si, X., and Narasimhan, K. τ <sup>2</sup>-bench: Evaluating conversational agents in a
dualcontrol environment, 2025. URL https://arxiv.

org/abs/2506.07982.

Cai, Y., Cai, S., Shi, Y., Xu, Z., Chen, L., Qin, Y., Tan, X., Li, G., Li, Z., Lin, H., Mao, Y., Li, K., and Sun, X.
Training-free group relative policy optimization. CoRR, abs/2510.08191, 2025a. doi: 10.48550/ARXIV. 2510.08191.
URL https://doi.org/10.48550/ arXiv.2510.08191.

Cai, Z., Guo, X., Pei, Y., Feng, J., Chen, J., Zhang, Y., Ma, W., Wang, M., and Zhou, H. FLEX: continuous agent
evolution via forward learning from experience. CoRR, abs/2511.06449, 2025b. doi: 10.48550/ARXIV. 2511.06449.
URL https://doi.org/10.48550/ arXiv.2511.06449.

Cao, Z., Deng, J., Yu, L., Zhou, W., Liu, Z., Ding, B., and Zhao, H. Remember me, refine me: A dynamic procedural memory
framework for experience-driven agent evolution, 2025. URL https://arxiv.org/abs/ 2512.10696.

Chen, T., Li, Y., Solodko, M., Wang, S., Jiang, N., Cui, T., Hao, J., Ko, J., Abdali, S., Xu, L., Zheng, S., Fan, H.,
Cameron, P., Wagle, J., and Koishida, K. Cua-skill: Develop skills for computer using agent, 2026.
URL https://arxiv.org/abs/2601.21123.

DeepSeek-AI. Deepseek-v3.2: Pushing the frontier of open large language models. CoRR, abs/2512.02556, 2025. doi:
10.48550/ARXIV.2512.02556. URL https:// doi.org/10.48550/arXiv.2512.02556.

Dou, S., Zhang, M., Yin, Z., Huang, C., Shen, Y., Wang, J., Chen, J., Ni, Y., Ye, J., Zhang, C., Xie, H., Hu, J., Wang,
S., Wang, W., Xiao, Y., Liu, Y., Xu, Z., Guo, Z., Zhou, P., Gui, T., Wu, Z., Qiu, X., Zhang, Q., Huang, X., Jiang,
Y.-G., Wang, D., and Yao, S. Cl-bench: A benchmark for context learning, 2026. URL https: //arxiv.org/abs/2602.03587.

Du, X., Li, L., Zhang, D., and Song, L. Memr<sup>3</sup>: Memory retrieval via reflective reasoning for llm agents,
2025. URL https://arxiv.org/abs/2512.20237.

Fang, J., Deng, X., Xu, H., Jiang, Z., Tang, Y., Xu, Z., Deng, S., Yao, Y., Wang, M., Qiao, S., Chen, H., and Zhang, N.
Lightmem: Lightweight and efficient memoryaugmented generation, 2025a. URL https://arxiv. org/abs/2510.18866.

Fang, J., Peng, Y., Zhang, X., Wang, Y., Yi, X., Zhang, G., Xu, Y., Wu, B., Liu, S., Li, Z., Ren, Z., Aletras, N., Wang,
X., Zhou, H., and Meng, Z. A comprehensive survey of self-evolving AI agents: A new paradigm bridging foundation models
and lifelong agentic systems. CoRR, abs/2508.07407, 2025b. doi: 10.48550/ARXIV.

2508.07407. URL https://doi.org/10.48550/ arXiv.2508.07407.

Fang, R., Liang, Y., Wang, X., Wu, J., Qiao, S., Xie, P., Huang, F., Chen, H., and Zhang, N. Memp: Exploring agent
procedural memory. CoRR, abs/2508.06433, 2025c. doi: 10.48550/ARXIV.2508.06433. URL https://
doi.org/10.48550/arXiv.2508.06433.

Gallego, V. Distilling feedback into memory-as-a-tool, 2026. URL https://arxiv.org/abs/2601.05960.

Gao, H., Geng, J., Hua, W., Hu, M., Juan, X., Liu, H., Liu, S., Qiu, J., Qi, X., Wu, Y., Wang, H., Xiao, H., Zhou, Y.,
Zhang, S., Zhang, J., Xiang, J., Fang, Y., Zhao, Q., Liu, D., Ren, Q., Qian, C., Wang, Z., Hu, M., Wang, H., Wu, Q., Ji,
H., and Wang, M. A survey of selfevolving agents: On path to artificial super intelligence. CoRR, abs/2507.21046, 2025.
doi: 10.48550/ARXIV. 2507.21046. URL https://doi.org/10.48550/ arXiv.2507.21046.

Gao, L., Ma, X., Lin, J., and Callan, J. Precise zero-shot dense retrieval without relevance labels, 2022.
URL https://arxiv.org/abs/2212.10496.

Guo, J., Yang, L., Chen, P., Xiao, Q., Wang, Y., Juan, X., Qiu, J., Shen, K., and Wang, M. Genenv: Difficultyaligned
co-evolution between llm agents and environment simulators, 2025. URL https://arxiv.org/abs/ 2512.19682.

Han, D., Couturier, C., D´ıaz, D. M., Zhang, X., Ruhle,¨ V., and Rajmohan, S. Legomem: Modular procedural memory for
multi-agent LLM systems for workflow automation. CoRR, abs/2510.04851, 2025. doi: 10.48550/ARXIV.2510.04851.
URL https://doi. org/10.48550/arXiv.2510.04851.

He, W., Sun, Y., Hao, H., Hao, X., Xia, Z., Gu, Q., Han, C., Zhao, D., Su, H., Zhang, K., Gao, M., Su, X., Cai, X., Cai,
X., Yang, Y., and Zhao, Y. Vitabench: Benchmarking llm agents with versatile interactive tasks in real-world
applications, 2025. URL https://arxiv.org/abs/ 2509.26490.

Hu, Y., Liu, S., Yue, Y., Zhang, G., Liu, B., Zhu, F., Lin, J., Guo, H., Dou, S., Xi, Z., Jin, S., Tan, J., Yin, Y.,
Liu, J., Zhang, Z., Sun, Z., Zhu, Y., Sun, H., Peng, B., Cheng, Z., Fan, X., Guo, J., Yu, X., Zhou, Z., Hu, Z., Huo, J.,
Wang, J., Niu, Y., Wang, Y., Yin, Z., Hu, X., Liao, Y., Li, Q., Wang, K., Zhou, W., Liu, Y., Cheng, D., Zhang, Q., Gui,
T., Pan, S., Zhang, Y., Torr, P., Dou, Z., Wen, J.-R., Huang, X., Jiang, Y.-G., and Yan, S. Memory in the age of ai
agents, 2025. URL https://arxiv.org/ abs/2512.13564.

Jiang, G., Su, Z., Qu, X., and Fung, Y. R. Xskill: Continual learning from experience and skills in multimodal agents,
2026. URL https://arxiv.org/abs/ 2603.12056.

Latimer, C., Boschi, N., Neeser, A., Bartholomew, C., Srivastava, G., Wang, X., and Ramakrishnan, N. Hindsight is 20/20:
Building agent memory that retains, recalls, and reflects, 2025. URL https://arxiv.org/ abs/2512.12818.

Li, J., Zhao, W., Zhao, J., Zeng, W., Wu, H., Wang, X., Ge, R., Cao, Y., Huang, Y., Liu, W., Liu, J., Su, Z., Guo, Y.,
Zhou, F., Zhang, L., Michelini, J., Wang, X., Yue, X., Zhou, S., Neubig, G., and He, J. The tool decathlon: Benchmarking
language agents for diverse, realistic, and long-horizon task execution, 2025. URL https:// arxiv.org/abs/2510.25726.

Li, X. When single-agent with skills replace multi-agent systems and when they fail. CoRR, abs/2601.04748, 2026. doi:
10.48550/ARXIV.2601.04748. URL https:// doi.org/10.48550/arXiv.2601.04748.

Li, X., Chen, W., Liu, Y., Zheng, S., Chen, X., He, Y., Li, Y., You, B., Shen, H., Sun, J., Wang, S., Zeng, Q., Wang,
D., Zhao, X., Wang, Y., Chaim, R. B., Di, Z., Gao, Y., He, J., He, Y., Jing, L., Kong, L., Lan, X., Li, J., Li, S., Li,
Y., Lin, Y., Liu, X., Liu, X., Lyu, H., Ma, Z., Wang, B., Wang, R., Wang, T., Ye, W., Zhang, Y., Xing, H., Xue, Y.,
Dillmann, S., and Lee, H. Skillsbench: Benchmarking how well agent skills work across diverse tasks. CoRR,
abs/2602.12670, 2026. doi: 10.48550/ ARXIV.2602.12670. URL https://doi.org/10. 48550/arXiv.2602.12670.

Liang, Y., Zhong, R., Xu, H., Jiang, C., Zhong, Y., Fang, R., Gu, J.-C., Deng, S., Yao, Y., Wang, M., Qiao, S., Xu, X.,
Wu, T., Wang, K., Liu, Y., Bi, Z., Lou, J., Jiang, Y. E., Zhu, H., Yu, G., Hong, H., Huang, L., Xue, H., Wang, C., Wang,
Y., Shan, Z., Chen, X., Tu, Z., Xiong, F., Xie, X., Zhang, P., Gui, Z., Liang, L., Zhou, J., Wu, C., Shang, J., Gong,
Y., unyu Lin, Xu, C., Deng, H., Zhang, W., Ding, K., Zhang, Q., Huang, F., Zhang, N., Pan, J. Z., Qi, G., Wang, H., and
Chen, H. Skillnet: Create, evaluate, and connect ai skills, 2026. URL https://arxiv.org/ abs/2603.04448.

Liu, Z., Cai, Y., Zhu, X., Zheng, Y., Chen, R., Wen, Y., Wang, Y., E, W., and Chen, S. Ml-master: Towards ai-for-ai via
integration of exploration and reasoning. CoRR, abs/2506.16499, 2025. doi: 10.48550/ARXIV. 2506.16499.
URL https://doi.org/10.48550/ arXiv.2506.16499.

Mai, S., Zhai, Y., Chen, Z., Chen, C., Zou, A., Tao, S., Liu, Z., and Ding, B. Cues: A curiosity-driven and

environment-grounded synthesis framework for agentic rl, December 2025. URL https://arxiv.org/abs/ 2512.01311.

Mialon, G., Fourrier, C., Swift, C., Wolf, T., LeCun, Y., and Scialom, T. Gaia: a benchmark for general ai assistants,
2023. URL https://arxiv.org/abs/ 2311.12983.

Ni, J., Liu, Y., Liu, X., Sun, Y., Zhou, M., Cheng, P., Wang, D., Zhao, E., Jiang, X., and Jiang, G. Trace2skill:
Distill trajectory-local lessons into transferable agent skills, 2026. URL https://arxiv.org/abs/ 2603.25158.

Novikov, A., Vu, N., Eisenberger, M., Dupont, E., Huang, P., Wagner, A. Z., Shirobokov, S., Kozlovskii, B., Ruiz, F. J.
R., Mehrabian, A., Kumar, M. P., See, A., Chaudhuri, S., Holland, G., Davies, A., Nowozin, S., Kohli, P., and Balog, M.
Alphaevolve: A coding agent for scientific and algorithmic discovery. CoRR, abs/2506.13131, 2025. doi:
10.48550/ARXIV.2506.13131. URL https:// doi.org/10.48550/arXiv.2506.13131.

OpenAI. System Card for o3-mini, 2025. URL https:// openai.com/index/o3-mini-system-card/. Accessed on December 11,
2025.

Ou, Y., Luo, Y., Zheng, J., Wei, L., Qiao, S., Zhang, J., Zheng, D., Chen, H., and Zhang, N. Automind: Adaptive
knowledgeable agent for automated data science. CoRR, abs/2506.10974, 2025. doi: 10.48550/ARXIV. 2506.10974.
URL https://doi.org/10.48550/ arXiv.2506.10974.

Ouyang, S., Yan, J., Hsu, I., Chen, Y., Jiang, K., Wang, Z., Han, R., Le, L. T., Daruki, S., Tang, X., Tirumalashetty,
V., Lee, G., Rofouei, M., Lin, H., Han, J., Lee, C., and Pfister, T. Reasoningbank: Scaling agent self-evolving with
reasoning memory. CoRR, abs/2509.25140, 2025. doi: 10.48550/ARXIV.2509.25140. URL https://
doi.org/10.48550/arXiv.2509.25140.

Patil, S. G., Mao, H., Cheng-Jie Ji, C., Yan, F., Suresh, V., Stoica, I., and E. Gonzalez, J. The berkeley function
calling leaderboard (bfcl): From tool use to agentic evaluation of large language models. In Forty-second International
Conference on Machine Learning, 2025.

Qiao, S., Zhao, Y., Qiu, Z., Wang, X., Zhang, J., Bin, Z., Zhang, N., Jiang, Y., Xie, P., Huang, F., and Chen, H.
Scaling generalist data-analytic agents. CoRR, abs/2509.25084, 2025. doi: 10.48550/ARXIV. 2509.25084.
URL https://doi.org/10.48550/ arXiv.2509.25084.

Ramrakhya, R., Szot, A., Attia, O., Yang, Y., Nguyen, A., Mazoure, B., Gan, Z., Agrawal, H., and Toshev, A. Scaling
synthetic task generation for agents via exploration. CoRR, abs/2509.25047, 2025. doi: 10.48550/ARXIV. 2509.25047.
URL https://doi.org/10.48550/ arXiv.2509.25047.

Shi, D., Cao, J., Chen, Q., Sun, W., Li, W., Lu, H., Dong, F., Qin, T., Zhu, K., Liu, M., Yang, J., Zhang, G., Liu, J.,
Zhang, C., Wang, J., Jiang, Y. E., and Zhou, W. Taskcraft: Automated generation of agentic tasks. CoRR, abs/2506.10055,
2025. doi: 10.48550/ARXIV. 2506.10055. URL https://doi.org/10.48550/ arXiv.2506.10055.

Sutton, Richard S., D. S. Welcome to the Era of Experience, April 2025.

Tang, X., Qin, T., Peng, T., Zhou, Z., Shao, D., Du, T., Wei, X., Xia, P., Wu, F., Zhu, H., et al. Agent kb: Leveraging
cross-domain experience for agentic problem solving. arXiv preprint arXiv:2507.06229, 2025. URL https:
//arXiv.org/abs/2507.06229.

Team, ., Zeng, A., Lv, X., Zheng, Q., Hou, Z., Chen, B., Xie, C., Wang, C., Yin, D., Zeng, H., Zhang, J., Wang, K.,
Zhong, L., Liu, M., Lu, R., Cao, S., Zhang, X., Huang, X., Wei, Y., Cheng, Y., An, Y., Niu, Y., Wen, Y., Bai, Y., Du,
Z., Wang, Z., Zhu, Z., Zhang, B., Wen, B., Wu, B., Xu, B., Huang, C., Zhao, C., Cai, C., Yu, C., Li, C., Ge, C., Huang,
C., Zhang, C., Xu, C., Zhu, C., Li, C., Yin, C., Lin, D., Yang, D., Jiang, D., Ai, D., Zhu, E., Wang, F., Pan, G., Wang,
G., Sun, H., Li, H., Li, H., Hu, H., Zhang, H., Peng, H., Tai, H., Zhang, H., Wang, H., Yang, H., Liu, H., Zhao, H.,
Liu, H., Yan, H., Liu, H., Chen, H., Li, J., Zhao, J., Ren, J., Jiao, J., Zhao, J., Yan, J., Wang, J., Gui, J., Zhao,
J., Liu, J., Li, J., Li, J., Lu, J., Wang, J., Yuan, J., Li, J., Du, J., Du, J., Liu, J., Zhi, J., Gao, J., Wang, K.,
Yang, L., Xu, L., Fan, L., Wu, L., Ding, L., Wang, L., Zhang, M., Li, M., Xu, M., Zhao, M., Zhai, M., Du, P., Dong, Q.,
Lei, S., Tu, S., Yang, S., Lu, S., Li, S., Li, S., Shuang-Li, Yang, S., Yi, S., Yu, T., Tian, W., Wang, W., Yu, W., Tam,
W. L., Liang, W., Liu, W., Wang, X., Jia, X., Gu, X., Ling, X., Wang, X., Fan, X., Pan, X., Zhang, X., Zhang, X., Fu,
X., Zhang, X., Xu, Y., Wu, Y., Lu, Y., Wang, Y., Zhou, Y., Pan, Y., Zhang, Y., Wang, Y., Li, Y., Su, Y., Geng, Y., Zhu,
Y., Yang, Y., Li, Y., Wu, Y., Li, Y., Liu, Y., Wang, Y., Li, Y., Zhang, Y., Liu, Z., Yang, Z., Zhou, Z., Qiao, Z., Feng,
Z., Liu, Z., Zhang, Z., Wang, Z., Yao, Z., Wang, Z., Liu, Z., Chai, Z., Li, Z., Zhao, Z., Chen, W., Zhai, J., Xu, B.,
Huang, M., Wang, H., Li, J., Dong, Y., and Tang, J. Glm-4.5: Agentic, reasoning, and coding (arc) foundation models,
2025a. URL https://arxiv.org/abs/2508.06471.

Team, K., Bai, Y., Bao, Y., Chen, G., Chen, J., Chen, N.,

Chen, R., Chen, Y., Chen, Y., Chen, Y., Chen, Z., Cui, J., Ding, H., Dong, M., Du, A., Du, C., Du, D., Du, Y., Fan, Y.,
Feng, Y., Fu, K., Gao, B., Gao, H., Gao, P., Gao, T., Gu, X., Guan, L., Guo, H., Guo, J., Hu, H., Hao, X., He, T., He,
W., He, W., Hong, C., Hu, Y., Hu, Z., Huang, W., Huang, Z., Huang, Z., Jiang, T., Jiang, Z., Jin, X., Kang, Y., Lai, G.,
Li, C., Li, F., Li, H., Li, M., Li, W., Li, Y., Li, Y., Li, Z., Li, Z., Lin, H., Lin, X., Lin, Z., Liu, C., Liu, C.,
Liu, H., Liu, J., Liu, J., Liu, L., Liu, S., Liu, T. Y., Liu, T., Liu, W., Liu, Y., Liu, Y., Liu, Y., Liu, Y., Liu, Z.,
Lu, E., Lu, L., Ma, S., Ma, X., Ma, Y., Mao, S., Mei, J., Men, X., Miao, Y., Pan, S., Peng, Y., Qin, R., Qu, B., Shang,
Z., Shi, L., Shi, S., Song, F., Su, J., Su, Z., Sun, X., Sung, F., Tang, H., Tao, J., Teng, Q., Wang, C., Wang, D.,
Wang, F., Wang, H., Wang, J., Wang, J., Wang, J., Wang, S., Wang, S., Wang, Y., Wang, Y., Wang, Y., Wang, Y., Wang, Y.,
Wang, Z., Wang, Z., Wang, Z., Wei, C., Wei, Q., Wu, W., Wu, X., Wu, Y., Xiao, C., Xie, X., Xiong, W., Xu, B., Xu, J.,
Xu, J., Xu, L. H., Xu, L., Xu, S., Xu, W., Xu, X., Xu, Y., Xu, Z., Yan, J., Yan, Y., Yang, X., Yang, Y., Yang, Z., Yang,
Z., Yang, Z., Yao, H., Yao, X., Ye, W., Ye, Z., Yin, B., Yu, L., Yuan, E., Yuan, H., Yuan, M., Zhan, H., Zhang, D.,
Zhang, H., Zhang, W., Zhang, X., Zhang, Y., Zhang, Y., Zhang, Y., Zhang, Y., Zhang, Y., Zhang, Y., Zhang, Z., Zhao, H.,
Zhao, Y., Zheng, H., Zheng, S., Zhou, J., Zhou, X., Zhou, Z., Zhu, Z., Zhuang, W., and Zu, X. Kimi k2: Open agentic
intelligence, 2025b. URL https://arxiv.org/abs/2507.20534.

Trivedi, H., Khot, T., Hartmann, M., Manku, R., Dong, V., Li, E., Gupta, S., Sabharwal, A., and Balasubramanian, N.
Appworld: A controllable world of apps and people for benchmarking interactive coding agents. In Ku, L., Martins, A.,
and Srikumar, V. (eds.), Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume
1: Long Papers), ACL 2024, Bangkok, Thailand, August 11-16, 2024, pp. 16022–16076. Association for Computational
Linguistics, 2024. doi: 10. 18653/V1/2024.ACL-LONG.850. URL https://doi. org/10.18653/v1/2024.acl-long.850.

Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L., and Anandkumar, A. Voyager: An open-ended
embodied agent with large language models. Trans. Mach. Learn. Res., 2024, 2024. URL https:
//openreview.net/forum?id=ehfRiF0R3a.

Wang, J., Yan, Q., Wang, Y., Tian, Y., Mishra, S. S., Xu, Z., Gandhi, M., Xu, P., and Cheong, L. L. Reinforcement
learning for self-improving agent with skill library. CoRR, abs/2512.17102, 2025a. doi: 10.48550/ARXIV. 2512.17102.
URL https://doi.org/10.48550/ arXiv.2512.17102.

Wang, J., Ming, Y., Ke, Z., Joty, S., Albarghouthi, A., and Sala, F. Skillorchestra: Learning to route agents via skill

transfer. CoRR, abs/2602.19672, 2026a. doi: 10.48550/ ARXIV.2602.19672. URL https://doi.org/10. 48550/arXiv.2602.19672.

Wang, Q., Cheng, Z., Zhang, S., Liu, F., Xu, R., Lian, H., Wang, K., Yu, X., Yin, J., Hu, S., Hu, Y., Zhang, S., Liu,
Y., Chen, R., and Wang, H. Memgovern: Enhancing code agents through learning from governed human experiences, 2026b.
URL https://arxiv.org/abs/ 2601.06789.

Wang, Z. Z., Gandhi, A., Neubig, G., and Fried, D. Inducing programmatic skills for agentic tasks. CoRR, abs/2504.06821,
2025b. doi: 10.48550/ARXIV. 2504.06821. URL https://doi.org/10.48550/ arXiv.2504.06821.

Wang, Z. Z., Mao, J., Fried, D., and Neubig, G. Agent workflow memory. In Forty-second International Conference on
Machine Learning, ICML 2025, Vancouver, BC, Canada, July 13-19, 2025. OpenReview.net, 2025c.
URL https://openreview.net/forum? id=NTAhi2JEEE.

Xia, P., Chen, J., Yang, X., Tu, H., Liu, J., Xiong, K., Han, S., Qiu, S., Ji, H., Zhou, Y., Zheng, Z., Xie, C., and
Yao, H. Metaclaw: Just talk – an agent that meta-learns and evolves in the wild, 2026. URL https://arxiv.
org/abs/2603.17187.

Xing, H., Zhuang, H., Zhao, X., Huang, Y., Tang, Z., and Zhang, X. Recipes for agents: Understanding skills and their
open questions. Preprint, ResearchGate. doi, 10, 2026.

Xu, W., Liang, Z., Mei, K., Gao, H., Tan, J., and Zhang, Y. A-mem: Agentic memory for llm agents, 2025.
URL https://arxiv.org/abs/2502.12110.

Yang, A., Li, A., Yang, B., Zhang, B., Hui, B., Zheng, B., Yu, B., Gao, C., Huang, C., Lv, C., Zheng, C., Liu, D., Zhou,
F., Huang, F., Hu, F., Ge, H., Wei, H., Lin, H., Tang, J., Yang, J., Tu, J., Zhang, J., Yang, J., Yang, J., Zhou, J.,
Zhou, J., Lin, J., Dang, K., Bao, K., Yang, K., Yu, L., Deng, L., Li, M., Xue, M., Li, M., Zhang, P., Wang, P., Zhu, Q.,
Men, R., Gao, R., Liu, S., Luo, S., Li, T., Tang, T., Yin, W., Ren, X., Wang, X., Zhang, X., Ren, X., Fan, Y., Su, Y.,
Zhang, Y., Zhang, Y., Wan, Y., Liu, Y., Wang, Z., Cui, Z., Zhang, Z., Zhou, Z., and Qiu, Z. Qwen3 technical report,
2025. URL https: //arxiv.org/abs/2505.09388.

Yang, C., Sun, Z., Wei, W., and Hu, W. Beyond static summarization: Proactive memory extraction for llm agents, 2026a.
URL https://arxiv.org/abs/ 2601.04463.

Yang, Y., Li, J., Pan, Q., Zhan, B., Cai, Y., Du, L., Zhou, J., Chen, K., Chen, Q., Li, X., Zhang, B., and He, L.
Autoskill: Experience-driven lifelong learning via skill self-evolution, 2026b. URL https://arxiv.org/ abs/2603.01145.

Yao, S., Chen, H., Yang, J., and Narasimhan, K. Webshop: Towards scalable real-world web interaction with grounded
language agents, 2023. URL https:// arxiv.org/abs/2207.01206.

Yao, S., Shinn, N., Razavi, P., and Narasimhan, K. τ -bench: A benchmark for tool-agent-user interaction in real-world
domains, 2024. URL https://arxiv.org/abs/ 2406.12045.

Yao, Y., Qin, J., Zhang, N., Xu, H., Zhu, Y., Yu, Z., Wang, M., Tang, Y., Gu, J.-C., Deng, S., Peng, N., and Chen, H.
Rethinking knowledge editing in reasoning era. Authorea Preprints, 2025. URL https://doi.org/10.
36227/techrxiv.176240454.46531513/v1.

Yue, Y., Chen, Z., Lu, R., Zhao, A., Wang, Z., Yue, Y., Song, S., and Huang, G. Does reinforcement learning really
incentivize reasoning capacity in llms beyond the base model?, 2025. URL https://arxiv.org/ abs/2504.13837.

Yuksekgonul, M., Bianchi, F., Boen, J., Liu, S., Huang, Z., Guestrin, C., and Zou, J. Textgrad: Automatic
”differentiation” via text, 2024. URL https://arxiv.org/ abs/2406.07496.

Zhai, Y., Tao, S., Chen, C., Zou, A., Chen, Z., Fu, Q., Mai, S., Yu, L., Deng, J., Cao, Z., Liu, Z., Ding, B., and Zhou,
J. Agentevolver: Towards efficient self-evolving agent system, 2025. URL https://arxiv.org/abs/2511. 10395.

Zhang, G., Fu, M., Wan, G., Yu, M., Wang, K., and Yan, S. G-memory: Tracing hierarchical memory for multi-agent systems,
2025a. URL https://arxiv.org/abs/ 2506.07398.

Zhang, G., Ren, H., Zhan, C., Zhou, Z., Wang, J., Zhu, H., Zhou, W., and Yan, S. Memevolve: Meta-evolution of agent
memory systems, 2025b. URL https://arxiv. org/abs/2512.18746.

Zhang, H., Fan, S., Zou, H. P., Chen, Y., Wang, Z., Zhou, J., Li, C., Huang, W.-C., Yao, Y., Zheng, K., Liu, X., Li, X.,
and Yu, P. S. Evoskills: Self-evolving agent skills via co-evolutionary verification, 2026a. URL https:
//arxiv.org/abs/2604.01687.

Zhang, H., Long, Q., Bao, J., Feng, T., Zhang, W., Yue, H., and Wang, W. Memskill: Learning and evolving memory skills
for self-evolving agents. CoRR, abs/2602.02474,

2026b. doi: 10.48550/ARXIV.2602.02474. URL https: //doi.org/10.48550/arXiv.2602.02474.

Zhang, Q., Hu, C., Upasani, S., Ma, B., Hong, F., Kamanuru, V., Rainton, J., Wu, C., Ji, M., Li, H., Thakker, U., Zou,
J., and Olukotun, K. Agentic context engineering: Evolving contexts for self-improving language models, 2025c.
URL https://arxiv.org/abs/2510.04618.

Zhang, Y., Li, M., Long, D., Zhang, X., Lin, H., Yang, B., Xie, P., Yang, A., Liu, D., Lin, J., Huang, F., and Zhou, J.
Qwen3 embedding: Advancing text embedding and reranking through foundation models. arXiv preprint arXiv:2506.05176,
2025d. URL https:// arxiv.org/abs/2506.05176.

Zhao, A., Huang, D., Xu, Q., Lin, M., Liu, Y., and Huang, G. Expel: LLM agents are experiential learners. In Wooldridge,
M. J., Dy, J. G., and Natarajan, S. (eds.), Thirty-Eighth AAAI Conference on Artificial Intelligence, AAAI 2024,
Thirty-Sixth Conference on Innovative Applications of Artificial Intelligence, IAAI 2024, Fourteenth Symposium on
Educational Advances in Artificial Intelligence, EAAI 2014, February 20-27, 2024, Vancouver, Canada, pp. 19632–19642.
AAAI Press, 2024. doi: 10.1609/AAAI.V38I17.29936. URL https://doi. org/10.1609/aaai.v38i17.29936.

Zheng, D., Du, L., Su, J., Tian, Y., Zhu, Y., Zhang, J., Wei, L., Zhang, N., and Chen, H. Knowledge augmented complex
problem solving with large language models: A survey. CoRR, abs/2505.03418, 2025. doi: 10.48550/ ARXIV.2505.03418.
URL https://doi.org/10. 48550/arXiv.2505.03418.

Zheng, L., Wang, R., Wang, X., and An, B. Synapse: Trajectory-as-exemplar prompting with memory for computer control. In
The Twelfth International Conference on Learning Representations, ICLR 2024, Vienna, Austria, May 7-11, 2024.
OpenReview.net, 2024. URL https: //openreview.net/forum?id=Pc8AU1aF5e.

Zheng, Y., Zhang, Z., Ma, C., Yu, Y., Zhu, J., Wu, Y., Xu, T., Dong, B., Zhu, H., Huang, R., and Yu, G. Skillrouter:
Skill routing for llm agents at scale, 2026. URL https: //arxiv.org/abs/2603.22455.

Zhou, H., Chen, Y., Guo, S., Yan, X., Lee, K. H., Wang, Z., Lee, K. Y., Zhang, G., Shao, K., Yang, L., and Wang, J.
Memento: Fine-tuning llm agents without fine-tuning llms, 2025. URL https://arxiv.org/ abs/2508.16153.

Zhou, H., Guo, S., Liu, A., Yu, Z., Gong, Z., Zhao, B., Chen, Z., Zhang, M., Chen, Y., Li, J., Yang, R., Liu, Q., Yu,
X., Zhou, J., Wang, N., Sun, C., and Wang, J. Memento-skills: Let agents design agents, 2026a.
URL https://arxiv.org/abs/2603.18743.

Zhou, S., Xu, F. F., Zhu, H., Zhou, X., Lo, R., Sridhar, A., Cheng, X., Ou, T., Bisk, Y., Fried, D., Alon, U., and
Neubig, G. Webarena: A realistic web environment for building autonomous agents, 2024. URL https:
//arxiv.org/abs/2307.13854.

Zhou, T., Liu, D., Yuan, L., Shao, J., and Hu, X. Colleague.skill: Automated ai skill generation via expert knowledge
distillation, 2026b. URL https://github.com/titanwings/ colleague-skill/blob/main/colleague_ skill.pdf.

## A. Detailed Experiments Settings

## A.1. Benchmark Details

BFCL-v3 Berkeley Function Calling Leaderboard V3 (BFCL-v3) (Patil et al., 2025) is a benchmark for evaluating function
calling and tool use in large language models. It emphasizes multi-turn interaction and multi-step reasoning. The
benchmark contains over 1,800 test instances and supports multiple programming languages, including Python, Java, and
JavaScript. Models are required to generate valid API calls and handle non-trivial interaction patterns. Evaluation
considers both structural validity and functional correctness. We first check whether the generated code is
syntactically valid using Abstract Syntax Tree analysis, and then execute it to verify that the outputs match the
expected results. A task is considered successful only when the agent produces all required function calls with correct
syntax and returns the correct computationa outcomes. In this work, we report Avg@4, which measures the average task
success rate across four independent trials, and Pass@4, which measures the probability that at least one of the four
trials succeeds.

Appworld AppWorld (Trivedi et al., 2024) is a benchmark suite for evaluating function calling agents and interactive
coding systems in realistic application environments. It simulates an ecosystem of nine widely used applications, such
as email services, music streaming platforms, and payment systems, and provides 457 API endpoints together with activity
data from around 100 virtual users. Tasks in AppWorld are typically long-horizon and require executing extended
sequences of interdependent actions. Many tasks involve discovering appropriate APIs rather than directly reusing
familiar patterns, which places additional demands on exploration and planning. The benchmark also exhibits a noticeable
distribution gap between training and test sets, where API usage patterns and task structures in the test set differ
from those observed during training. In addition, task execution is tightly coupled with the evolving environment state.
Intermediate actions modify the system state and influence future decisions, which increases sensitivity to planning
errors and makes robust multi-step reasoning more difficult. Evaluation is based on state-driven unit tests that assess
task completion from multiple aspects. AppWorld provides both task-level and scenario-level metrics. In this work, we
use Task Goal Completion as the primary measure of performance. Following the standard protocol, we report Avg@4 and
Pass@4 across four independent trials.

τ <sup>2</sup>-Bench τ <sup>2</sup>-Bench (Barres et al., 2025) evaluates tool use in conversational agent settings,
with a strong emphasis on user-agent interaction. The benchmark simulates multi-turn dialogues between a user and an
agent, aiming to reflect realistic conversational behavior. Agents must track dialogue context across turns, interpret
user requests, select and invoke APIs appropriately, and follow domain-specific business rules. The tasks cover domains
such as airline customer service and retail customer service. The interactive nature of the benchmark requires agents to
respond to user feedback, maintain coherent dialogue flow, and coordinate tool use with the ongoing conversation.
Performance is assessed based on task completion accuracy, correctness of tool use, and compliance with policies. In
this work, we conduct four independent trials per task and report Pass@1 on each of the three domains.

## A.2. Baseline Details

A-Mem A-Mem (Xu et al., 2025) is an agentic memory framework that equips LLM-based agents with the ability to maintain
and utilize long-term knowledge over extended interactions. The method organizes accumulated experiences into a
memory-centric structure, enabling agents to selectively retain, retrieve, and revise stored information according to
task objectives and observed outcomes. Rather than treating memory as a passive log, A-Mem emphasizes autonomous memory
management driven by the agent’s goals and interaction context. In our experiments, we reproduce A-Mem based on its
publicly available implementation, with minor prompt adaptations to support memory writing and organization during task
interactions.

AWM AWM (Agent Workflow Memory) (Wang et al., 2025c) is a memory-augmented agent framework that focuses on discovering
reusable workflow patterns from past task executions. The method stores completed task trajectories as episodic
experiences and derives higher-level procedural knowledge by analyzing multiple successful examples. Experience
retrieval follows a lightweight lexical matching strategy. Textual representations of task queries and stored
experiences are mapped to sparse term-based vectors, and relevance is measured using cosine similarity. A small set of
highly relevant experiences is selected for downstream analysis, with subsampling applied when multiple candidates
exhibit comparable similarity. Workflow induction is performed by prompting a language model to analyze the retrieved
successful trajectories and summarize recurring action patterns. Rather than relying on explicit symbolic rules or
predefined workflow schemas, AWM captures reusable procedural structures directly from empirical task executions.
Retrieved experiences are incorporated as conversational message objects (e.g., HumanMessage and AIMessage), enabling
the language model to process exemplar interactions naturally within the dialogue context.

ExpeL ExpeL (Zhao et al., 2024) is an experience-driven learning framework that improves agent performance by reflecting
on past successes and failures. The method stores task execution trajectories and generates experiential knowledge by
contrasting successful and unsuccessful outcomes for the same task. In our experiments, we reproduce ExpeL by collecting
both successful trajectories (reward ≥ 1.0) and failed trajectories (reward < 1.0). For each successful example, a small
number of failed trajectories from the same task type are selected for comparative analysis. A large language model is
prompted to analyze the paired trajectories and generate natural-language critiques that highlight key decision
differences and improvement suggestions. These critiques are retained as unstructured textual experiences and reused as
guidance in subsequent tasks.

## A.3. Implementation Details

Skills Extraction. During the experience extraction stage, which comprises both reasoning and experience extraction, we
employ GLM-4.6 with a temperature of 0.9. For each task in the training set, we independently sample four trajectories.
Environment feedback exceeding 1500 tokens is summarized. We cluster the extracted skills using DBSCAN (Density-Based
Spatial Clustering of Applications with Noise) with a cosine similarity threshold of 0.9. For each cluster, we truncate
the skill set to at most 15 skills. Skill updates are performed with up to three iterative refinement rounds. During the
skill expansion stage, we set the exploration model temperature to 1.0 and perform 1 time to explore environment for
each training task.

Skills Usage. We build a skill semantic vector store using FAISS with an HNSW index under cosine similarity (via
L2-normalized embeddings and inner-product search). At query time, we first perform a broad retrieval of the Top-100
nearest skills. Candidates are then filtered by a hybrid relevance threshold: we keep only results whose cosine
similarity is at least 0.45, and also within 0.08 of the best match for that query, ensuring both a minimum quality
floor and adaptive selectivity. To reduce near-duplicate skills, we apply semantic deduplication by removing items whose
pairwise cosine similarity exceeds 0.95, retaining the higher-scoring representative. Finally, we return up to 8 skills
after applying Maximal Marginal Relevance (MMR) for diversity-aware selection, using a relevance–diversity trade-off
weight of 0.75 to emphasize relevance while mitigating redundancy.

## B. Case Study For SkillX

We present case studies across three diverse benchmarks: AppWorld (Trivedi et al., 2024), BFCL (Patil et al., 2025), and
τ <sup>2</sup>-bench (Barres et al., 2025). These cases show that skill libraries help agents avoid common failures such
as incorrect API call sequences, missing prerequisite checks, and the inability to handle conversational topic shifts.
By framing domain knowledge as reusable skills, agents can complete complex multi-step tasks that the baseline method
fails, reducing trial and error from multiple failed attempts to successful execution on the first attempt.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/cf20cd1466b3d10a86d7bd8db899ba697d9d88e1459870d4adf4360a25b4544d.jpg)

Figure 4. AppWorld benchmark case study: Updating Spotify playlist based on roommates’ suggestions. SkillX successfully
handles API call sequences (pagination pattern for playlist retrieval) and cross-app integration (integrating Spotify
and Phone APIs), while the baseline without multi-level skills fails due to incorrect API call sequences and inability
to complete cross-app integration tasks.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/5dec3681b46a91077ec08f4be0e327e21822c5edfa1638988fc782bf84099063.jpg)

Figure 5. BFCL benchmark case study: Vehicle engine start safety check and Twitter posting. SkillX follows prerequisite
sequences (lock doors → press brake pedal → start engine) and properly authenticates before posting tweets, while the
baseline without multi-level skills fails by calling APIs without prerequisites and encountering tool calling errors.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/abe15df37b3385094cda935db78ee532934189ad8404b1fcc427e78500ce4568.jpg)

Figure 6. τ <sup>2</sup>-bench case study: Requesting delay flight compensation in airline domain. SkillX handles topic
shifts, retrieves user reservations without reservation numbers, verifies flight delays, and executes the compensation
workflow, while the baseline without multi-level skills fails to recognize topic shifts and cannot retrieve reservation
details.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/fd9a6623a8572085969121deb43619acb949bcf340c6ea92e85bd8cfcfa7a505.jpg)

Table 4. Prompt for filtering skills based on quality criteria.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/16a8694ba80439ed77b5af196e74ef4ec6ce36ad067ddb5d8b5fd2ee8813e640.jpg)

Table 5. Prompt for summarizing environment feedback from agent interactions.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/bf7218757e1b0287f47da10a7273488e726848b7c29ccbd5f082f68a023bdd65.jpg)

Table 6. Prompt for validating tool invocations against specifications.

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/b6ccfcc566907fd253187eae80ba3db7fb5ce147b739411e8f73712542d9388f.jpg)

Table 7. Prompt for extracting reusable plans from agent trajectories.

## C.5. Merge Prompt

## Merge Prompt

You are a code expert. Your task is to analyze a list of skills, merge skills that are meaningfully similar, and
decompose complex skills into smaller atomic skills while preserving behavior and intent.

## Input Description

The user will provide a list of skills.

## Skill Definition Rule

• Skill is a dictionary with four keys: name, document, content and tools.

1. name: the skill’s name.

2. document: the skill’s functionality, the key parameters, the final output of the skill, and any important notes.

3. content: the concrete implementation of the skill.

4. tools: the key tools used in the skill (list).

• The skill is abstract, modular, and reusable. Specifically, the skill name must be generic under one application (
e.g., {good example} instead of {bad example}. The skill must use parameters instead of hard-coded values (e.g.,
specific email address {email address}). The skill body must be self-contained.

• Explicitly declare the key parameters and the final output data types using type hints. Example: Parameters: param:
str; Outputs: output: list[dict]:

• Include a detailed description of the skill with input and output explanation.

• The skill should not be similar to the existing skills in the skills library.

• The skill must involve multiple processing steps. Simply using the result of an API call without additional logic does
not qualify as a valid skill.

• Never call other skills from the skills library or any previously defined skills.

• Do not import any Python packages.

• Avoid a functional style; there’s no need to use return.

## Good skill:

```txt
json
{
    "name": {name},
    "document": {document},
    "content": {content},
    "tools": {tools}
} 
```

## Focus

1. Focus on skills with similar names and similar skillality.

2. Carefully analyze the concrete implementation differences between similar skills.

## Merge Guidelines

1. Generality: Merge skills that have similar names and similar skillality. The merged skill should use a generic name,
   and its Notes and implementation should cover all plausible variants and edge cases.

2. Atomicity: If skills have a containment relationship (one skill’s skillality subsumes or builds on another), follow
   the skill definitions to preserve atomicity and avoid merging.

3. Merge Constraints: Any merged skill must comply with the skill definition rules, especially atomicity and
   reusability, and should avoid being tied to a specific task or scenario.

## Decompose Guidelines

1. Atomicity: Only decompose skills whose skillality is overly complex (e.g., they include skillality already covered by
   other provided skills) into smaller sub-skills.

2. Generality: The decomposed skills must follow the skill-definition rules and remain reusable—avoid coupling them to
   any specific task or scenario.

## Output Format

Output a list containing the skills (with one or multiple skills) from merging and/or decomposing the skills in the
input skill list as follows:

## <skill>

Note: You don’t necessarily need to both merge and decompose. You may choose to only merge them into a single skill.

Table 8. Prompt for merging and decomposing skills.

## C.6. Atomic Skill Extract Prompt

## Atomic Skill Extract Prompt

An agent system is provided with a skills library and has tried to solve the task multiple times with a successful
solution. Review the task-solving attempt and extract generalizable skills.

## 1. Inputs Description

• User Task

• Trajectory: A record of an agent’s interactions successfully with the environment as it attempts to complete a user
task.

• skills library: A collection of all currently available skills that can be directly reused.

• Specific-Tool: Given a specific tool, extract only one reusable skill for the specified tool.

## 2. Skill Definition Rule

• Skill is a dictionary with four keys: name, document, content and tools.

1. name: the specific tool’s name.

2. document: the tool’s functionality, the key parameters, the final output of the skill, and any important notes.

3. content: the tool’s usage examples, and examples of combining it with other tools (if applicable).

4. tools: the key tools used in the content (list).

• The skill is centered around a specific tool, describing its core functionality, important notes, and common usage
examples.

• Explicitly declare the key parameters and the final output data types using type hints. Example: Parameters: param:
str; Outputs: output: dict:

• Include a detailed description of the skill with input and output explanation.

• The skill should not be similar to the existing skills in the skills library.

• The parameters used in content must be reusable instead of hard-coded values (e.g., specific email address
”jay@gmail.com”)

• The usage examples of content may involve one or more tool uses.

• The document must clearly and thoroughly document all relevant details of the specific tool use.

• Never call other skills from the skills library or any previously defined skills.

• Do not import any Python packages.

• Avoid a functional style and Python code style; there’s no need to use return.

## 3. Update Existing Skills

Your goal is to ensure the system retains actionable skills that help it behave correctly in the future.

You have three options: [modify, add, keep]

• modify: revise an existing skill to make it more effective (e.g., improving documents). Only change content when
necessary, and ensure the resulting skill remains broadly general-purpose.

• add: introduce a new skill only when the existing skills library is missing the specified tool.

• keep: Preserve the skill unchanged when there are no clear issues.

Common actions:

• add a new skill

• update a skill’s usage instructions/documentation

• revise a skill’s variable/parameter definitions to make it more generalizable

• keep a skill unchanged

## 4. Requirements for each skill that is modified or added.

• Avoid duplication: If a skills library is provided, do not add new skills that are similar to existing ones—use keep
or modify instead.

• Ensure domain specificity: The skill must contain domain-specific tool.

• Specific-Tool guided extraction: Only focus on the specified tool in the trajectory when extracting skills.

## 5. Good Skill Example

{example}

## 6. Output Format

You will finish by returning in this JSON format as follows:

```json
You will finish by returning in this JSON format as follows:
```json
[
{
"option": "modify",
"skill": "the modified skill",
"modified_from": "spotify get all user playlists" # specify the skill name of existing skills that is modified
},
{
"option": "add",
"skill": "the added skill",
}
] 
```

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/f6f2d722893643bd1a4b1063fc7e7468aac598c0efe953bf11f43940263aa4e9.jpg)

Table 9. Prompt for atomic skill extraction based on specific tools.

## C.7. Functional Skill Extract Prompt

![image](https://cdn-mineru.openxlab.org.cn/result/2026-07-12/a6186265-7e30-41d9-8d19-d1b5e86aa6d7/1dda5073eb50508e07419be37ee1f613a6ceb7e9582404bd4ea94bce315a3d5d.jpg)

```txt
4. Requirements for each skill that is modified or added.
- Avoid duplication: If a skills library is provided, do not add new skills that are similar to existing ones—use keep or modify instead.
- Exclude non-solution behavior: Do not include capability exploration, debugging activities, or any failed/incorrect steps.
- Ensure domain specificity: The skill must reference domain-specific libraries/APIs, e.g., {api}.
- Avoid over-wrapping: Verify the implementation is not merely a thin wrapper around another skill (i.e., not just calling a single underlying skill without meaningful additional logic).
- Specific-step guided extraction: Only focus on the specified step in the trajectory when extracting skills.

5. Good Skill Example
{example}

6. Output Format
You will finish by returning in this JSON format as follows:
```json
[
    {
    "option": "modify",
    "skill": "the modified skill",
    "modified_from": "spotify get all user playlists" # specify the skill name of existing skills that is modified
    },
    {
    "option": "add",
    "skill": "the added skill",
    },
    {
    "option": "keep",
    "skill_name": "the kept skill name",
    },
...
] 
```