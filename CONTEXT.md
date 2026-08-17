# Deepmemory Domain

Deepmemory manages conversational continuity across sessions by separating current state, task progress, durable knowledge, short-lived history, and traceable source material.

## Language

**Main Task**:
The durable unit of work that a primary conversation advances across one or more sessions.
_Avoid_: Workspace, chat topic

**Subtask**:
A bounded piece of a Main Task executed by the primary agent or delegated to a subagent, with its result validated by the primary agent.
_Avoid_: Temporary session, separate conversation card

**Conversation Card**:
The current, compact state of one conversation working on a Main Task or a daily topic; it is versioned once per assistant turn.
_Avoid_: Workspace card, complete task history

**Task Board**:
The durable lifecycle view of a Main Task and its Subtasks across planned, ready, in-progress, completed, and failed states.
_Avoid_: Conversation Card, execution log

**Planning Intention**:
A near-term direction that has not yet been decomposed into an executable plan.
_Avoid_: Next step, ready action

**Ready Action**:
A next step whose method, dependencies, and completion criteria are sufficiently clear to execute.
_Avoid_: Planning intention

**Suspended Topic**:
A recently active daily topic temporarily removed from the foreground while retaining a fast path for resumption.
_Avoid_: Archived topic, abandoned task

**Semantic Memory**:
A durable, independently understandable fact, preference, constraint, or adopted decision that can affect future behavior.
_Avoid_: Raw chat, process log

**Short-Term Memory**:
A time-limited record of a recent question, event, candidate plan, or recalled cold memory that may be useful again soon.
_Avoid_: Durable memory, current context

**Process Memory**:
A grouped account of what was attempted in one assistant turn, including result, failure, and key output.
_Avoid_: Raw tool trace, semantic memory

**Source Record**:
The original message, tool result, or Harness trajectory referenced by a memory or card revision for audit and detail recovery.
_Avoid_: Automatically injected memory

**Cold Memory**:
An expired Short-Term Memory or Process Memory excluded from normal injection but searchable through deliberate recall.
_Avoid_: Deleted memory, archive

**Compressed Archive**:
A low-frequency historical summary created from old cold memories while retaining references to their Source Records.
_Avoid_: Cold memory, physical deletion

**Disputed Memory**:
A memory that conflicts with current conversation or stronger evidence and is suspended from automatic injection until user confirmation.
_Avoid_: Invalid memory, superseded memory

**Invalid Memory**:
A user-designated rule or proposal known to be wrong and unsafe to follow again.
_Avoid_: Rejected proposal, failed attempt

**Rejected Proposal**:
A proposal not adopted in its current discussion, retained for historical comparison without being treated as permanently wrong.
_Avoid_: Invalid memory

**Recall**:
An agent-initiated, read-only search that progressively retrieves active memory, cold memory, compressed archives, and source records when current context lacks detail.
_Avoid_: Automatic injection, full history replay
