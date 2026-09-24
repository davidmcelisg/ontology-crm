# Personal CRM: Ontology Design

An ontology-driven personal CRM. The type system lives in data, not in code. The
application reads these declarations at runtime; adding an object type is an edit
to a declaration file, not a code change.

---

## 1. Design principles

These four rules explain every decision below. If a later choice seems arbitrary,
it follows from one of these.

**P1. Objects are thin, relationships are fat.**
Anything you might want to qualify with a date, a source, a strength, or an
end is reified into its own object type. `Person` holds almost nothing, because
almost nothing about a person is permanently true.

**P2. Never store a state that can be derived.**
"Owes me a reply" is not a field. It is a Function over `Interaction` objects. A
stored status column drifts out of sync with reality; a derived one cannot.

**P3. Every assertion is a claim, not a fact.**
Personal network data is hearsay with an expiry date. Objects are never mutated
in place. A change is a new claim that supersedes an old one, carrying who told
you and when. The store is append-only; current state is a fold over the claim log.

**P4. Scope grows by adding data, not types.**
Family is not a type. Family is a `Relationship` with `kind: family`. If a new
part of your life requires a new object type, the model is wrong.

---

## 2. Object types

Eight types. Identifiers are human-readable slugs (`person:ana-ruiz`,
`org:bain-cdmx`) so that prompts and Actions can reference objects by name and so
that a demo reads well on screen.

### 2.1 `Person`

Identity only. Deliberately close to empty.

```yaml
Person:
  id:            string   # person:ana-ruiz
  display_name:  string
  given_name:    string?
  family_name:   string?
  aliases:       [string] # "Ana", "Ana R."; feeds entity resolution
  based_in:      string?  # a claim about them, not derivable from affiliations
  notes:         text?
```

No employer, no job title, no phone-call history. Those are all
`Affiliation`, `Interaction`, or claims.

**You are a `Person` in this graph**, not a privileged root node. Traversal stays
uniform and costs nothing. A single config constant names you:
`self_person_id: person:david`. `direction` on interactions is interpreted
relative to that constant.

### 2.2 `Organization`

```yaml
Organization:
  id:        string      # org:bain-cdmx
  name:      string
  kind:      enum        # company | university | student_org | nonprofit
                         # | government | other
  location:  string?
  parent:    ref(Organization)?   # org:bain-cdmx -> org:bain
  notes:     text?
```

`parent` lets you model an office or a subsidiary without a new type (P4), and
lets "who do I know at Bain" roll up across offices.

### 2.3 `Affiliation`: reified Person ↔ Organization

```yaml
Affiliation:
  id:            string
  person:        ref(Person)
  organization:  ref(Organization)
  role_title:    string?     # "Senior Consultant"
  kind:          enum        # employee | intern | student | founder | member
                             # | alumnus | contractor | advisor
  seniority:     enum?       # ic | senior | manager | director | vp | executive
  start_date:    date?
  end_date:      date?       # absent means current (P2, no is_current field)
```

Reified because you care about employment *history*, not just the present. "Which
HackMTY sponsor contacts are senior enough to matter now" is a query over
`Affiliation`, not over `Person`.

### 2.4 `Relationship`: reified, **directed** Person → Person

```yaml
Relationship:
  id:                   string
  from_person:          ref(Person)
  to_person:            ref(Person)
  kind:                 enum    # family | friend | colleague | classmate
                                # | mentor | mentee | recruiter | acquaintance
  strength:             int?    # 1-5, as perceived by from_person
  origin_context:       string? # "met at HackMTY 2023 sponsor dinner"
  origin_interaction:   ref(Interaction)?
```

Directed, because relationships are asymmetric in reality. You may consider
Andrés a strong contact while he recalls one conversation. A symmetric model
halves the storage and lies about the world. Two records are written when the
tie genuinely runs both ways.

### 2.5 `Interaction`

The atom of any CRM.

```yaml
Interaction:
  id:                 string
  occurred_at:        datetime
  channel:            enum    # in_person | call | video | email | text
                              # | whatsapp | linkedin | other
  direction:          enum    # outbound | inbound | mutual (relative to self)
  participants:       [ref(Person)]
  subject:            string?
  summary:            text?
  expects_response:   bool    # drives the open-thread Function
  in_reply_to:        ref(Interaction)?
  about:              [ref(Pursuit | Organization)]
```

`in_reply_to` makes threads a chain rather than a guess, which is what makes
"who owes me a reply" computable instead of heuristic.

### 2.6 `Introduction`

```yaml
Introduction:
  id:                   string
  introducer:           ref(Person)
  introduced_a:         ref(Person)
  introduced_b:         ref(Person)
  occurred_at:          date
  context:              text?
  resulting_interaction: ref(Interaction)?
```

Three participants, so this **cannot** be an edge under any modelling scheme. It
is the cleanest possible argument for reification, and worth saying out loud in
an interview. It also makes reciprocity queryable: introductions you made versus
introductions you received.

### 2.7 `Pursuit`

Any directed effort toward an outcome. Job applications live here.

```yaml
Pursuit:
  id:                   string
  kind:                 enum    # job_application | recruiting_process
                                # | business_development | fellowship
                                # | grad_school
  target_organization:  ref(Organization)
  target_role:          string?
  stage:                string  # free text, e.g. "first round", "onsite"
  outcome:              enum    # open | offer | rejected | withdrawn | stale
  opened_at:            date
  closed_at:            date?
  referred_by:          ref(Person)?
  originated_in:        ref(Introduction)?
```

Note what is *not* here: a stage-history table. Because claims are append-only and
timestamped (P3), the full stage history falls out of the claim log for free. Each
`AdvancePursuit` writes a new claim; replaying claims for that Pursuit gives you
the timeline. This is the clearest payoff of the claim model, and `scripts/demo.py`
prints it: the `why do I believe` section replays whichever object has the
richest claim history, which on the sample network is exactly this.

### 2.8 `Commitment`

```yaml
Commitment:
  id:            string
  obligor:       ref(Person)    # who owes
  obligee:       ref(Person)    # who is owed
  description:   string         # "send my resume"
  created_in:    ref(Interaction)?
  due_date:      date?
  fulfilled_by:  ref(Interaction)?   # absent means outstanding (P2)
```

Symmetric machinery answers both "what did I promise and drop" and "what am I
waiting on," depending on which side `self_person_id` sits.

### 2.9 Naming objects with no natural name

`Person.display_name` and `Organization.name` are titles anyone would
recognize as a name. `Relationship` and `Introduction` are not. `kind` is an
enum like `acquaintance`, and `context` is a sentence, not a name. Declaring
either as `title_attribute` would make `path_to` output read like a database
dump instead of a sentence.

So neither type declares one. Where a type has no usable `title_attribute`,
rendering composes a title from whoever the object connects to instead:
`Introduction by Teo Marin`. The rule lives once, in `query.title`, and costs
the type nothing, since omitting `title_attribute` is enough to get the composed
form.

A title also seeds the id, and here the two kinds of title differ again. A
second `Person` named Rosa Delgado is almost certainly the same Rosa, so
`Person` and `Organization` declare `title_is_identity: true` and a colliding
create is rejected as a duplicate. A second `Affiliation` titled "Software
Engineer" or a second `Interaction` titled "Coffee" is a different object, so
for every other type minting appends a counter: `aff:software-engineer-2`.

---

## 3. Link types

Declared with cardinality so that validation is generic. The engine enforces
these without any per-type code. Every `ref` attribute in section 2 derives
exactly one row here; nothing here is written by hand.

| Link | From | To | Cardinality |
|---|---|---|---|
| `affiliation_of` | Affiliation | Person | many-to-one |
| `affiliation_at` | Affiliation | Organization | many-to-one |
| `relationship_from` | Relationship | Person | many-to-one |
| `relationship_to` | Relationship | Person | many-to-one |
| `relationship_origin` | Relationship | Interaction | many-to-one |
| `org_parent` | Organization | Organization | many-to-one |
| `interaction_participant` | Interaction | Person | many-to-many |
| `interaction_reply_to` | Interaction | Interaction | many-to-one |
| `interaction_about` | Interaction | Pursuit \| Organization | many-to-many |
| `intro_introducer` | Introduction | Person | many-to-one |
| `intro_party_a` | Introduction | Person | many-to-one |
| `intro_party_b` | Introduction | Person | many-to-one |
| `intro_result` | Introduction | Interaction | many-to-one |
| `pursuit_target` | Pursuit | Organization | many-to-one |
| `pursuit_referrer` | Pursuit | Person | many-to-one |
| `pursuit_origin` | Pursuit | Introduction | many-to-one |
| `commitment_obligor` | Commitment | Person | many-to-one |
| `commitment_obligee` | Commitment | Person | many-to-one |
| `commitment_created_in` | Commitment | Interaction | many-to-one |
| `commitment_fulfilled_by` | Commitment | Interaction | many-to-one |

`intro_party_a` / `intro_party_b` are two many-to-one links rather than one
many-to-many, because `introduced_a` and `introduced_b` are separate
attributes. The `distinct` axiom in section 5 only requires the three
participants differ, so which one is `introduced_a` versus `introduced_b`
carries no meaning beyond which attribute holds it.

---

## 4. The claim envelope

No object is stored directly. Every object body is wrapped in a claim.

```yaml
Claim:
  id:             string
  object_type:    string        # "Affiliation"
  object_id:      string        # the object this claim is about
  body:           object        # the attributes, per section 2

  asserted_at:    datetime      # when I learned it
  valid_from:     date?         # when it became true in the world
  valid_to:       date?         # when it stopped being true

  source_kind:    enum          # self_observed | told_by_person | document
                                # | inferred | prompt
  source_person:  ref(Person)?  # who told me
  source_note:    text?         # verbatim prompt text, email subject, etc.

  supersedes:     ref(Claim)?
  is_retraction:  bool          # true when this claim withdraws `supersedes`
  redirect_to:    string?       # set by MergePersons; reads follow it, the
                                # loser's claims stay in the log untouched
```

Two time axes, not one. `asserted_at` is when the claim entered your world;
`valid_from` / `valid_to` is when the fact held in the world. They come apart
constantly ("turns out she left Bain last year"), and separating them is what lets
you answer *what do I believe, why, and how old is that belief*.

**Resolution rule.** The current state of an object is the body of its
latest non-retracted claim by `asserted_at` whose validity window contains `now`.
Storage is an append-only log; state is a fold over it.

---

## 5. Actions

The only permitted way to change the ontology. Each has typed parameters,
validation, and declared effects. The LLM ingestion layer (section 6) emits
these and nothing else, which means it cannot produce an invalid graph.

| Action | Effect | Required | Optional |
|---|---|---|---|
| `CreatePerson` | creates Person | display_name | given_name, family_name, aliases, based_in, notes |
| `CreateOrganization` | creates Organization | name, kind | location, parent, notes |
| `UpdateOrganization` | updates Organization | organization | name, kind, location, parent, notes |
| `AssertAffiliation` | creates Affiliation | person, organization, kind | role_title, seniority, start_date, end_date |
| `EndAffiliation` | updates Affiliation | affiliation, end_date | (none) |
| `AssertRelationship` | creates Relationship | from_person, to_person, kind | strength, origin_context, origin_interaction |
| `RecordInteraction` | creates Interaction | occurred_at, channel, direction, participants | subject, summary, expects_response, in_reply_to, about |
| `RecordIntroduction` | creates Introduction | introducer, introduced_a, introduced_b, occurred_at | context, resulting_interaction |
| `OpenPursuit` | creates Pursuit | kind, target_organization, stage, outcome, opened_at | target_role, closed_at, referred_by, originated_in |
| `AdvancePursuit` | updates Pursuit | pursuit, stage | outcome, target_role |
| `MakeCommitment` | creates Commitment | obligor, obligee, description | created_in, due_date, fulfilled_by |
| `FulfillCommitment` | updates Commitment | commitment, fulfilled_by | (none) |
| `MergePursuits` | entity resolution | keep, merge | (none) |
| `MergePersons` | entity resolution | keep, merge | (none) |

Actions declaring `parameters: inherit` take the full attribute list of the type
they create, which is why the required column above is longer than it looks in
the yaml. `EndAffiliation` writes a claim carrying `valid_to`; `AdvancePursuit`
writes a claim, so stage history is free.

Every Action carries the claim metadata from section 4: `source_kind`,
`source_person`, `source_note`, `asserted_at`.

`UpdateOrganization` is the one action that names its own target. The executor
normally infers which parameter holds the object being updated: an updating
action has exactly one `ref` parameter pointing at the type it updates. That
inference is ambiguous for a self-referencing type, because `Organization.parent`
is also a ref to `Organization`. Rather than special-case it in code, the action
declares `target_parameter: organization` and the declaration wins. Any future
updating action on a self-referencing type gets the same treatment for free.

`MergePersons` and `MergePursuits` exist because free-text ingestion will
inevitably create `person:ana` and `person:ana-ruiz` as separate objects, or open
a second Pursuit for a role that was really the same application. Aliases plus an
explicit merge Action is the cheap, honest answer to entity resolution.

Neither action carries logic of its own. `special: entity_resolution` routes both
to the same executor, and the redirect machinery in the store only requires that
both objects share a type. Covering a second type was one declaration, which is
the argument for keeping mutations declarative in the first place.

### Validation

Generic checks derived from the declarations above:

- required attributes present, enum values in range
- referenced objects exist and are of the declared type
- link cardinality respected

Plus a small set of hand-written axioms:

- `Introduction`: all three persons distinct
- `Relationship`: `from_person != to_person`
- `Affiliation`: `end_date >= start_date`
- `Claim`: `valid_to >= valid_from`

---

## 6. Ingestion: plain text to Actions

`crm/ingest.py` is where free-text notes become the Actions from section 5.
It is the payoff of Actions being the only mutation path: the model proposes,
the ontology decides what is legal, and nothing reaches the store that a
hand-written caller couldn't also have written.

Two phases, and nothing is written between them:

```
propose(store, text, complete) -> Proposal        # ask the model, then validate
apply(store, proposal)         -> [ActionResult]  # execute, in order
```

**The schema is generated, not written.** `action_schemas()` builds a JSON
schema per Action straight from the registry: properties, required fields,
enum values, ref targets. A new Action added to `ontology.yaml` reaches the
model on the next run with no prompt change.

**A roster does entity resolution before the model has to.** Every known
object is listed with its id, title, aliases, and up to three linked
neighbours, so "there are two Anas, but only one is linked to a recruiter
role" is answerable from the prompt. The model is told to reuse an id from the
roster rather than mint a new one, which is what keeps `person:ana` and
`person:ana-ruiz` from becoming two people most of the time. `MergePersons`
(section 5) is the fallback for when it doesn't.

**Ids are deterministic**, so the model can create an object and reference it
later in the same batch: `mint_id` slugifies the title attribute, so "Ana
Ruiz" as a `Person` is always `person:ana-ruiz`, computable before the object
exists, not just after.

**Validation runs twice against the same rules.** `propose` dry-runs every
action through the validator so a rejected action is visible before anything
is confirmed; `apply` re-validates for real at execution time. A proposal that
validates clean is guaranteed to apply clean.

**The model is injected, not imported.** `propose` takes a `complete(system,
user) -> text` function, so the whole pipeline runs against a canned stub with
no API key (`scripts/try_ingest.py` does exactly this), and swapping in
`anthropic_completer()` for a real model touches no other line.

This is also where P3 earns its keep on the ingestion side: every proposed
action carries `source_kind: "prompt"` and, when the note names who told you,
`source_person`, the same provenance fields a hand-typed Action needs, so a
claim that came from an LLM reading a note is indistinguishable in the log
from one you asserted yourself, except honestly labelled as `prompt` rather
than `self_observed`.

---

## 7. Functions

Derived answers. Nothing here is stored.

| Function | Question it answers |
|---|---|
| `open_threads()` | Who owes me a reply, and who am I ignoring |
| `outstanding_commitments(side)` | What I promised and dropped / what I'm owed |
| `who_do_i_know_at(org)` | Current affiliations at an org or its children, ranked by relationship strength |
| `path_to(target)` | Shortest route to a person or org, over any linked object type, typically Relationship, Affiliation, Introduction, or Commitment |
| `pursuit_board()` | All open pursuits, stage, and whether a reply is outstanding |
| `going_stale(months)` | Relationships with strength ≥ 3 and no interaction in N months |
| `why_do_i_believe(object)` | Full claim chain with sources and dates |
| `reciprocity(person)` | Introductions and favours exchanged in each direction |

**`open_threads` is the reference implementation of P2.** An interaction is an
open thread when the most recent interaction in a reply chain is outbound,
`expects_response` is true, and no inbound interaction follows it. There is no
`awaiting_reply` field anywhere in the model.

---

## 8. Known limitations

Cut deliberately, and each has a stated reason. Better to have seen a gap coming
than to be shown it.

1. **A changed ontology does not migrate existing claims.** `store._decode_body`
   deliberately skips the validator on load, because `changes` is a partial body, so
   every required attribute would report missing. The consequence is that
   renaming or removing a type breaks loading outright, and removing an
   attribute leaves values sitting unvalidated in history. Versioning the
   ontology and replaying the log through the new declarations is the fix; it
   is the largest gap in the design.
2. **Claims attach to whole objects, not individual attributes.** If Isa told you
   the employer and Ana told you the title, one claim covers both. Attribute-level
   provenance is the natural extension and requires no change to the type system,
   only a finer-grained claim body.
3. **No confidence scores.** `source_kind` is a coarse proxy. Adding a numeric
   confidence invites a weighting scheme nobody can justify.
4. **Functions are Python, not a rule DSL.** A declarative rule language is the
   right end state and the wrong thing to build first.
5. **Entity resolution is manual**, via `aliases` and `MergePersons`.
6. **`self` is a config constant**, so the graph is single-perspective. Modelling
   multiple viewpoints would mean parameterising `direction` and `strength`.
7. **Merging is declared per type, not available on all of them.** `Person` and
   `Pursuit` have merge actions; the other six do not, so two duplicate
   Organizations still cannot be merged. The redirect machinery in the store is
   type-agnostic, so each of these is a missing declaration rather than a
   missing mechanism.
8. **`direction: mutual` cannot open a thread.** `open_threads` sorts an
   interaction into "they owe me" on `outbound` and "I owe them" on `inbound`.
   A mutual interaction with `expects_response: true` is silently in neither
   list. Debts in a two-way conversation belong in a `Commitment`, which is
   what the model does; the gap is that nothing rejects the combination.
9. **A `Commitment` has exactly one obligor.** Two people jointly promising one
   thing has to be recorded as two commitments or attributed to one of them.
10. **Nothing scheduled can be represented.** `Interaction.occurred_at` is
   required and past-tense by construction, so "call booked for next week"
   lives in a `Pursuit.stage` string. A `scheduled_for` attribute would be the
   honest fix.
11. **No access control or encryption.** Relevant given the sensitivity of the
   data, and more so once ingestion (section 6) is wired to a real model, since
   `anthropic_completer()` sends note text to a third-party API. Out of scope
   for the first build.
12. **Reads are full scans.** `store.referrers` walks every object of every
   referring type to answer "what points at this". Fine at personal-network
   scale; the first thing to replace if the log ever grows.
