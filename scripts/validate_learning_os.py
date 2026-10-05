#!/usr/bin/env python3
"""Deterministic mechanical validator for Learning OS canonical YAML."""
from __future__ import annotations
import argparse, hashlib, json, re, stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import yaml

if __package__:
    from .intake_policy import validate_intake_preferences
else:
    from intake_policy import validate_intake_preferences

ROOTS={"config","runtime","learner","domains","topics","execution","coordination","evidence"}
# Current production-style V0.3 canonical YAML document types. Historical
# V0.2 learner-project types and V0.4 split-plane contract types are excluded.
LEGACY_CANONICAL_DOCUMENT_TYPES={
    "project_config","conversation_sequence_registry","lineage_control",
    "learner_background","learner_model","learner_calibration","learner_costs",
    "learner_execution","learner_knowledge","curriculum","topic_goal","topic_plan",
    "topic_progress","topic_deferred","subtopic_definition","subtopic_plan",
    "subtopic_progress","weekly_execution","daily_execution","execution_session",
    "branch_registry","branch_runtime","branch_report","coordination_event",
    "hub_runtime","topic_report","learning_handoff","evidence",
}
# Current production legacy V0.3 schema-version compatibility, compiled from
# production protocol/schema.md §2.1. No fallback/wildcard exists; the V0.4
# split Instance state schema axis is deliberately separate.
LEGACY_SCHEMA_VERSIONS={
    "project_config":{"0.3"},"conversation_sequence_registry":{"0.3"},"lineage_control":{"0.3"},
    "learner_background":{"0.1"},"learner_model":{"0.1"},"learner_calibration":{"0.1"},"learner_costs":{"0.1"},
    "learner_execution":{"0.3"},"learner_knowledge":{"0.3"},"curriculum":{"0.1"},
    "topic_goal":{"0.3"},"topic_plan":{"0.3"},"topic_progress":{"0.3"},"topic_deferred":{"0.3"},
    "subtopic_definition":{"0.3"},"subtopic_plan":{"0.3"},"subtopic_progress":{"0.3"},
    "weekly_execution":{"0.3"},"daily_execution":{"0.3"},"execution_session":{"0.3"},
    "branch_registry":{"0.3"},"branch_runtime":{"0.3"},"branch_report":{"0.3"},"coordination_event":{"0.3"},
    "hub_runtime":{"0.3"},"topic_report":{"0.3"},"learning_handoff":{"0.3"},"evidence":{"0.3"},
}
# Current production protocol/schema.md §2.3 canonical legacy storage registry.
# Rule order has no semantic meaning: callers must collect every full match.
_SEG=r"(?!(?:\.|\.\.)(?:/|$))[^/]+"
_YAML_SEG=r"(?!(?:\.|\.\.)\.yaml$)[^/]+\.yaml"
_CSEQ=r"C(?:0[1-9]|[1-9][0-9]+)"
LEGACY_CANONICAL_PATH_RULES=(
    (re.compile(r"config/project\.yaml"),"project_config"),
    (re.compile(r"runtime/ui/conversation-sequences\.yaml"),"conversation_sequence_registry"),
    (re.compile(rf"runtime/lineages/{_YAML_SEG}"),"lineage_control"),
    (re.compile(r"learner/background\.yaml"),"learner_background"),
    (re.compile(r"learner/model\.yaml"),"learner_model"),
    (re.compile(r"learner/calibration\.yaml"),"learner_calibration"),
    (re.compile(r"learner/costs\.yaml"),"learner_costs"),
    (re.compile(r"learner/execution\.yaml"),"learner_execution"),
    (re.compile(rf"learner/knowledge/{_YAML_SEG}"),"learner_knowledge"),
    (re.compile(rf"domains/{_SEG}/curriculum\.yaml"),"curriculum"),
    (re.compile(rf"topics/{_SEG}/goal\.yaml"),"topic_goal"),
    (re.compile(rf"topics/{_SEG}/plan\.yaml"),"topic_plan"),
    (re.compile(rf"topics/{_SEG}/progress\.yaml"),"topic_progress"),
    (re.compile(rf"topics/{_SEG}/deferred\.yaml"),"topic_deferred"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/definition\.yaml"),"subtopic_definition"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/plan\.yaml"),"subtopic_plan"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/progress\.yaml"),"subtopic_progress"),
    (re.compile(rf"execution/weekly/{_YAML_SEG}"),"weekly_execution"),
    (re.compile(rf"topics/{_SEG}/execution/daily/{_YAML_SEG}"),"daily_execution"),
    (re.compile(rf"topics/{_SEG}/execution/sessions/{_YAML_SEG}"),"execution_session"),
    (re.compile(rf"topics/{_SEG}/coordination/branches\.yaml"),"branch_registry"),
    (re.compile(rf"topics/{_SEG}/coordination/branches/{_SEG}/runtime\.yaml"),"branch_runtime"),
    (re.compile(rf"topics/{_SEG}/coordination/branches/{_SEG}/report\.yaml"),"branch_report"),
    (re.compile(rf"topics/{_SEG}/coordination/events/{_YAML_SEG}"),"coordination_event"),
    (re.compile(r"coordination/hub/runtime\.yaml"),"hub_runtime"),
    (re.compile(rf"topics/{_SEG}/coordination/topic-report\.yaml"),"topic_report"),
    (re.compile(rf"topics/{_SEG}/handoffs/{_SEG}/{_CSEQ}-to-{_CSEQ}\.yaml"),"learning_handoff"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/handoffs/{_SEG}/{_CSEQ}-to-{_CSEQ}\.yaml"),"learning_handoff"),
    (re.compile(rf"evidence/{_YAML_SEG}"),"evidence"),
)

def expected_types_for_path(p):
    """Return every canonical legacy document type whose storage rule matches p."""
    return tuple(t for rx,t in LEGACY_CANONICAL_PATH_RULES if rx.fullmatch(p))

PLANNED={"planned","in_progress","completed","blocked","deferred","dropped"}; CONF={"low","medium","high"}
CAP={"provisional","supported","conflicted","unsupported"}; EDIR={"support","challenge","neutral","deferred"}
GEN={"active","idle","handoff_pending","archived","deprecated"}; BROLE={"hub","main","practice","deep_dive"}; BLIFE={"active","idle","retired"}
TLIFE={"active","paused","completed","cancelled"}; SLIFE={"active","paused","completed","merged","split","discarded"}; SKIND={"standard","prerequisite_support","integration"}
NKIND={"concept","procedure","theorem","skill","representation","application","integration"}; EREL={"requires","supports","extends","contrasts_with","applies_to","integrates_with","generalizes","motivates","reinforces"}; ESTR={"weak","medium","strong"}

@dataclass(frozen=True)
class Finding:
    severity:str; code:str; path:str; message:str
    def render(self): return f"{self.severity.upper():7} {self.code:28} {self.path}: {self.message}"

def resolve_repository_relative_file(snapshot_root:Path,ref):
    """Resolve one repository-relative POSIX file without crossing trust boundaries.

    The path is syntax-checked before touching candidate filesystem entries,
    then every component is inspected with lstat so symlinks are rejected
    rather than followed.  The returned Path is safe to read only when reason
    is None.  Callers map the stable reason into their own Finding namespace.
    """
    if not isinstance(ref,str) or not ref or not ref.strip(): return None,"invalid","path must be a non-empty string"
    if ref!=ref.strip(): return None,"invalid","leading/trailing whitespace is not valid repository path syntax"
    if ref.startswith("~"): return None,"home","home-style paths are not repository-relative"
    if ref.startswith(("/","\\")): return None,"absolute","absolute/UNC-style paths are forbidden"
    if re.match(r"^[A-Za-z]:",ref): return None,"windows_drive","Windows drive path forms are forbidden"
    if "\\" in ref: return None,"backslash","repository paths must use POSIX '/' separators"
    parts=ref.split("/")
    if any(part=="" for part in parts): return None,"invalid","empty path segments are forbidden"
    if ".." in parts: return None,"traversal","'..' path segments are forbidden"
    if "." in parts: return None,"dot","'.' path segments are forbidden"
    try: root=Path(snapshot_root).resolve(strict=True)
    except (OSError,ValueError,RuntimeError) as e: return None,"filesystem",f"snapshot root resolution failed: {e.__class__.__name__}"
    current=root; mode=None
    for part in parts:
        current=current/part
        try: mode=current.lstat().st_mode
        except FileNotFoundError: return None,"missing","referenced file does not exist inside the snapshot"
        except (OSError,ValueError) as e: return None,"filesystem",f"filesystem inspection failed: {e.__class__.__name__}"
        if stat.S_ISLNK(mode): return None,"symlink","repository trust-boundary paths must not traverse symlinks"
    try:
        resolved=current.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError: return None,"missing","referenced file does not exist inside the snapshot"
    except ValueError: return None,"escape","resolved target escapes the snapshot root"
    except (OSError,RuntimeError) as e: return None,"filesystem",f"target resolution failed: {e.__class__.__name__}"
    if mode is None or not stat.S_ISREG(mode): return None,"not_file","referenced target must be an ordinary regular file"
    return resolved,None,None

def _generation_number(value):
    if isinstance(value,int) and not isinstance(value,bool) and value>=1: return value
    if isinstance(value,str) and re.fullmatch(r"[1-9][0-9]*",value): return int(value)
    return None

def _generation_record(generations,generation):
    if not isinstance(generations,dict) or not isinstance(generation,int): return None
    record=generations.get(generation,generations.get(str(generation)))
    return record if isinstance(record,dict) else None

def learning_handoff_identity_mismatches(runtime,generation_key,generation_record,handoff):
    """Return deterministic Branch-transition mismatches for one handoff_ref."""
    mismatches=[]
    source=_generation_number(generation_key)
    if source is None:
        mismatches.append(f"referencing generation key {generation_key!r} is not a positive integer generation")
    for field in ("topic","branch_id","lineage_id"):
        if handoff.get(field)!=runtime.get(field):
            mismatches.append(f"{field} {handoff.get(field)!r} != Branch runtime {runtime.get(field)!r}")
    if source is not None and handoff.get("from_generation")!=source:
        mismatches.append(f"from_generation {handoff.get('from_generation')!r} != referencing generation {source}")
    target=handoff.get("to_generation")
    if not isinstance(target,int) or isinstance(target,bool):
        mismatches.append(f"to_generation {target!r} is not an integer generation")
    elif source is not None and target<=source:
        mismatches.append(f"to_generation {target} must be later than source generation {source}")
    elif source is not None:
        active=runtime.get("active_generation")
        pending=runtime.get("pending_successor")
        lifecycle=generation_record.get("lifecycle") if isinstance(generation_record,dict) else None
        if source==active and lifecycle=="handoff_pending" and pending is not None:
            pending_value=pending.get("generation") if isinstance(pending,dict) else pending
            pending_generation=_generation_number(pending_value)
            if pending_generation is not None and target!=pending_generation:
                mismatches.append(f"to_generation {target} != pending successor generation {pending_generation}")
        elif _generation_record(runtime.get("generations") or {},target) is None:
            mismatches.append(f"to_generation {target} has no materialized successor generation record")
    return mismatches

class Validator:
    def __init__(self,root:Path): self.root=root.resolve(); self.docs={}; self.findings=[]; self.evidence=set(); self.curricula={}; self.schema_blocked=set()
    def error(self,c,p,m): self.findings.append(Finding("error",c,p,m))
    def enum(self,p,f,v,a):
        if v not in a: self.error("enum.invalid",p,f"{f}={v!r} not in {sorted(a)}")
    @staticmethod
    def expected(p):
        matches=expected_types_for_path(p)
        return matches[0] if len(matches)==1 else None
    def load(self):
        for f in sorted(self.root.rglob("*.yaml")):
            rp=f.relative_to(self.root)
            if not rp.parts or rp.parts[0] not in ROOTS: continue
            p=rp.as_posix()
            try:d=yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception as e:self.error("yaml.parse",p,str(e));continue
            if not isinstance(d,dict):self.error("yaml.mapping",p,"canonical YAML must be mapping");continue
            self.docs[p]=d
    def structural(self):
        req={"project_config":("project","repository","time","runtime","protocol"),"conversation_sequence_registry":("sequence_format","scopes"),"lineage_control":("lineage","active_generation","pending_handoff"),"learner_knowledge":("revision","domain","concepts"),"curriculum":("domain","curriculum_version","nodes"),"topic_goal":("revision","topic","goal"),"topic_plan":("revision","topic","plan"),"topic_progress":("revision","topic","plan_revision","lifecycle","milestones"),"subtopic_definition":("subtopic",),"subtopic_plan":("revision","topic","subtopic","plan"),"subtopic_progress":("revision","topic","subtopic","plan_revision","milestones"),"weekly_execution":("revision","window"),"branch_registry":("revision","topic","branches"),"branch_runtime":("revision","topic","branch_id","lineage_id","active_generation","pending_successor","generations"),"execution_session":("id","topic","branch","meaningful_learning"),"learning_handoff":("topic","branch_id","lineage_id","from_generation","to_generation"),"evidence":("id","observed_at","observation","interpretation","targets")}
        for p,d in self.docs.items():
            if "document_type" not in d:self.error("yaml.document_type",p,"missing document_type");self.schema_blocked.add(p);continue
            t=d["document_type"]
            if not isinstance(t,str) or not t.strip():
                self.error("yaml.document_type_invalid",p,f"document_type must be a non-empty, non-whitespace string; found {t!r}");self.schema_blocked.add(p);continue
            if t not in LEGACY_CANONICAL_DOCUMENT_TYPES:
                self.error("yaml.document_type_unknown",p,f"unknown/noncanonical document_type {t!r}");self.schema_blocked.add(p);continue
            if "schema_version" not in d:
                self.error("yaml.schema_version",p,"missing schema_version");self.schema_blocked.add(p);continue
            v=d["schema_version"]
            if not isinstance(v,str) or not v.strip():
                self.error("yaml.schema_version_invalid",p,f"schema_version must be a non-empty, non-whitespace string; found {v!r}");self.schema_blocked.add(p);continue
            if v not in LEGACY_SCHEMA_VERSIONS[t]:
                self.error("yaml.schema_version_unsupported",p,f"document_type {t!r} schema_version {v!r} is unsupported; accepted versions are {sorted(LEGACY_SCHEMA_VERSIONS[t])}");self.schema_blocked.add(p);continue
            matches=expected_types_for_path(p)
            if not matches:
                self.error("path.unregistered",p,f"no canonical storage family registered for {p}");self.schema_blocked.add(p);continue
            if len(matches)>1:
                self.error("path.ambiguous",p,f"multiple canonical storage families match {p}: {list(matches)}");self.schema_blocked.add(p);continue
            e=matches[0]
            if t!=e:
                self.error("path.document_type",p,f"expected {e}, found {t}");self.schema_blocked.add(p);continue
            if t in {"evidence","coordination_event"}:
                stem=Path(p).stem; ident=d.get("id")
                if not isinstance(ident,str) or ident!=stem:
                    self.error("path.identity",p,f"filename stem {stem!r} must equal document id {ident!r}");self.schema_blocked.add(p);continue
            if t=="evidence" and isinstance(d.get("id"),str):
                if d["id"] in self.evidence:self.error("evidence.duplicate_id",p,d["id"])
                self.evidence.add(d["id"])
            if t=="curriculum" and isinstance(d.get("domain"),dict) and isinstance(d["domain"].get("id"),str):self.curricula[d["domain"]["id"]]=d
            for k in req.get(t,()):
                if k not in d:self.error("document.required",p,f"missing {k}")
            if t=="topic_plan":self.enum(p,"plan.status",(d.get("plan")or{}).get("status"),{"awaiting_intake","provisional","active","paused"})
            elif t=="topic_progress":
                self.enum(p,"lifecycle",d.get("lifecycle"),TLIFE)
                for k,x in (d.get("milestones")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"milestones.{k}.status",x.get("status"),PLANNED)
            elif t=="subtopic_definition":s=d.get("subtopic")or{};self.enum(p,"subtopic.kind",s.get("kind"),SKIND);self.enum(p,"subtopic.lifecycle",s.get("lifecycle"),SLIFE)
            elif t=="subtopic_plan":self.enum(p,"plan.status",(d.get("plan")or{}).get("status"),{"provisional","active","paused"})
            elif t=="subtopic_progress":
                for k,x in (d.get("milestones")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"milestones.{k}.status",x.get("status"),PLANNED)
            elif t=="learner_knowledge":
                for c,x in (d.get("concepts")or{}).items():
                    for a,y in ((x or{}).get("capabilities")or{}).items():self.enum(p,f"{c}.{a}.state",y.get("state"),CAP);self.enum(p,f"{c}.{a}.confidence",y.get("confidence"),CONF)
            elif t=="evidence":
                x=d.get("interpretation")or{};self.enum(p,"interpretation.direction",x.get("direction"),EDIR)
                for k in ("diagnosticity","novelty","confidence"):self.enum(p,f"interpretation.{k}",x.get(k),CONF)
                if "classification_confidence" in x or "classification_confidence" in d:self.error("evidence.legacy_confidence",p,"use interpretation.confidence")
            elif t=="curriculum":
                for k,x in (d.get("nodes")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"nodes.{k}.kind",x.get("kind"),NKIND)
                for x in d.get("edges")or[]:
                    if isinstance(x,dict):self.enum(p,"edge.relation",x.get("relation"),EREL);self.enum(p,"edge.strength",x.get("strength"),ESTR)
            elif t=="branch_registry":
                for k,x in (d.get("branches")or{}).items():self.enum(p,f"branches.{k}.role",x.get("role"),BROLE);self.enum(p,f"branches.{k}.lifecycle",x.get("lifecycle"),BLIFE)
            elif t=="branch_runtime":
                for k,x in (d.get("generations")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"generations.{k}.lifecycle",x.get("lifecycle"),GEN)
            elif t=="weekly_execution":
                for s in ("baseline_outcomes","current_outcomes"):
                    for x in d.get(s)or[]:
                        if isinstance(x,dict) and "status" in x:self.enum(p,f"{s}.{x.get('id')}.status",x.get("status"),PLANNED)
    def refs(self):
        for p,d in self.docs.items():
            if p in self.schema_blocked:continue
            t=d.get("document_type")
            if t=="topic_progress":
                q=self.docs.get(f"topics/{d.get('topic')}/plan.yaml")
                if q and d.get("plan_revision")!=q.get("revision"):self.error("revision.topic_progress_plan",p,"plan_revision != current topic plan revision")
            elif t=="subtopic_progress":
                q=self.docs.get(f"topics/{d.get('topic')}/subtopics/{d.get('subtopic')}/plan.yaml")
                if q and d.get("plan_revision")!=q.get("revision"):self.error("revision.subtopic_progress_plan",p,"plan_revision != current subtopic plan revision")
            elif t=="topic_plan":
                q=self.docs.get(f"topics/{d.get('topic')}/goal.yaml");r=((d.get("plan")or{}).get("based_on")or{}).get("goal_revision")
                if q and isinstance(r,int) and r>q.get("revision",r):self.error("revision.topic_plan_goal_future",p,"goal_revision points to future revision")
            elif t=="subtopic_plan":
                q=self.docs.get(f"topics/{d.get('topic')}/plan.yaml");r=((d.get("plan")or{}).get("based_on")or{}).get("topic_plan_revision")
                if q and isinstance(r,int) and r>q.get("revision",r):self.error("revision.subtopic_plan_topic_future",p,"topic_plan_revision points to future revision")
                for m in (d.get("plan")or{}).get("milestones")or[]:
                    for r in (m or{}).get("curriculum_refs")or[]:
                        if not isinstance(r,dict) or r.get("type")!="curriculum_node":continue
                        c=self.curricula.get(r.get("domain"));n=r.get("id")
                        if not c:self.error("reference.curriculum_domain",p,f"missing curriculum {r.get('domain')}");continue
                        nodes,aliases=c.get("nodes")or{},c.get("aliases")or{};a=aliases.get(n)
                        if isinstance(a,dict):a=a.get("to")or a.get("target")or a.get("id")
                        if n not in nodes and (not isinstance(a,str) or a not in nodes):self.error("reference.curriculum_node",p,f"unresolved curriculum node {r.get('domain')}:{n}")
            elif t=="learner_knowledge":
                for c,x in (d.get("concepts")or{}).items():
                    for a,y in ((x or{}).get("capabilities")or{}).items():
                        for side in ("support","challenge"):
                            for r in ((y or{}).get("evidence_refs")or{}).get(side)or[]:
                                if r not in self.evidence:self.error("reference.evidence_missing",p,f"{c}.{a} -> {r}")
    @staticmethod
    def prod(s):return s in {"learning_os","learning_hub","model_review"} or bool(re.fullmatch(r"(?:topic_hub:[^:]+|learning_(?:main|practice|deep_dive):[^:]+:[^:]+)",s))
    def sequence(self):
        p="runtime/ui/conversation-sequences.yaml";d=self.docs.get(p)
        if not d or p in self.schema_blocked:return
        scopes=d.get("scopes")or{};np=False
        for s,x in scopes.items():
            if not isinstance(x,dict):self.error("sequence.scope_record",p,s);continue
            n=x.get("last_allocated")
            if not isinstance(n,int) or isinstance(n,bool) or n<0:self.error("sequence.last_allocated",p,s)
            if s.startswith(("acceptance:","test:")):np=True;self.prod(s.split(":",1)[1]) or self.error("sequence.nonproduction_scope",p,s)
            elif not self.prod(s):self.error("sequence.production_scope",p,s)
        if np and (d.get("nonproduction_sequence_format")or{}).get("prefix")!="T":self.error("sequence.nonproduction_prefix",p,"non-production prefix must be T")
        w=(d.get("sequence_format")or{}).get("minimum_width",2)
        for i,r in enumerate(d.get("repair_history")or[]):
            if not isinstance(r,dict):self.error("sequence.repair_record",p,str(i));continue
            a,b=r.get("previous_last_allocated"),r.get("repaired_last_allocated")
            if not self.prod(str(r.get("scope"))):self.error("sequence.repair_scope",p,str(i))
            if not all(isinstance(x,int) and not isinstance(x,bool) for x in (a,b)) or not 0<=b<a:self.error("sequence.repair_range",p,str(i));continue
            if r.get("orphaned_suffix")!=[f"C{n:0{w}d}" for n in range(b+1,a+1)]:self.error("sequence.repair_suffix",p,str(i))
            if any(not r.get(k) for k in ("reason","repaired_at","authority")):self.error("sequence.repair_provenance",p,str(i))
            cur=(scopes.get(r.get("scope"))or{}).get("last_allocated")
            if isinstance(cur,int) and cur<b:self.error("sequence.repair_current",p,str(i))
    def authority(self):
        for p,d in self.docs.items():
            if p in self.schema_blocked:continue
            t=d.get("document_type")
            if t=="lineage_control":
                a=d.get("active_generation");i=(d.get("bootstrap")or{}).get("initial_generation")
                if not isinstance(a,int) or isinstance(a,bool):self.error("lineage.active_generation",p,"must be integer");continue
                if isinstance(i,int) and a<i:self.error("lineage.initial_generation",p,"active < initial")
                h=d.get("pending_handoff")
                if h is not None:
                    if not isinstance(h,dict):self.error("lineage.pending_shape",p,"must be null or mapping");continue
                    if h.get("from_generation")!=a:self.error("lineage.pending_from",p,"from_generation != active_generation")
                    if not isinstance(h.get("to_generation"),int) or h["to_generation"]<=a:self.error("lineage.pending_to",p,"to_generation must advance")
                    if not (h.get("packet")or{}).get("path") or not (h.get("packet")or{}).get("blob_sha"):self.error("lineage.pending_packet",p,"missing packet identity")
                    z=h.get("anchor")or{}
                    if not z.get("repository") or not z.get("ref") or not z.get("canonical_head"):self.error("lineage.pending_anchor",p,"incomplete anchor")
                z=(d.get("last_transition")or{}).get("to_generation")
                if isinstance(z,int) and z>a:self.error("lineage.last_transition",p,"transition exceeds active generation")
            elif t=="branch_runtime":
                a=d.get("active_generation");g=d.get("generations")or{};r=g.get(a,g.get(str(a))) if isinstance(a,int) else None
                if r is None:self.error("branch.active_record",p,f"missing generation {a}");continue
                act=[k for k,x in g.items() if isinstance(x,dict) and x.get("lifecycle")=="active"];h=d.get("pending_successor")
                if h is None:
                    if len(act)!=1:self.error("branch.active_count",p,f"active={act}")
                    if r.get("lifecycle")!="active":self.error("branch.active_lifecycle",p,"active record not active")
                else:
                    n=h.get("generation") if isinstance(h,dict) else h
                    if r.get("lifecycle")!="handoff_pending":self.error("branch.pending_lifecycle",p,"source not handoff_pending")
                    if not isinstance(n,int) or n<=a:self.error("branch.pending_generation",p,"pending successor must advance")
                for generation_key,x in g.items():
                    if not isinstance(x,dict) or "handoff_ref" not in x: continue
                    ref=x.get("handoff_ref")
                    _,reason,message=resolve_repository_relative_file(self.root,ref)
                    if reason is not None:
                        self.error("branch.handoff_ref",p,f"generation {generation_key!r} handoff_ref {ref!r}: {reason}: {message}"); continue
                    if expected_types_for_path(ref)!=("learning_handoff",):
                        self.error("branch.handoff_ref_target",p,f"generation {generation_key!r} handoff_ref {ref!r} is not a canonical learning_handoff path"); continue
                    target=self.docs.get(ref)
                    if target is None or ref in self.schema_blocked or target.get("document_type")!="learning_handoff":
                        self.error("branch.handoff_ref_target",p,f"generation {generation_key!r} handoff_ref {ref!r} does not resolve to a structurally valid learning_handoff document"); continue
                    mismatches=learning_handoff_identity_mismatches(d,generation_key,x,target)
                    if mismatches:self.error("branch.handoff_ref_identity",p,f"generation {generation_key!r} handoff_ref {ref!r}: {'; '.join(mismatches)}")
    def weekly(self):
        for p,d in self.docs.items():
            if p in self.schema_blocked:continue
            if d.get("document_type")!="weekly_execution" or d.get("current_outcomes") is None or d.get("closing") is not None:continue
            x=d.get("projection")
            if not isinstance(x,dict):self.error("weekly.projection",p,"open current_outcomes require provenance");continue
            if not x.get("observed_at"):self.error("weekly.projection_observed_at",p,"missing observed_at")
            if x.get("reconciliation") not in {"read_time","explicit_reconciliation"}:self.error("weekly.projection_reconciliation",p,"invalid reconciliation")
            src=x.get("source_revisions")or[]
            if not isinstance(src,list) or not src:self.error("weekly.projection_sources",p,"missing sources");continue
            for s in src:
                if not isinstance(s,dict) or not s.get("ref") or not isinstance(s.get("revision"),int):self.error("weekly.projection_source",p,"invalid source");continue
                q=self.docs.get(s["ref"])
                if q is None:self.error("weekly.projection_source_missing",p,s["ref"])
                elif isinstance(q.get("revision"),int) and s["revision"]>q["revision"]:self.error("weekly.projection_source_future",p,s["ref"])
    def run(self):self.load();self.structural();self.refs();self.sequence();self.authority();self.weekly();return self.findings

# ===== V0.4 Core plane surface (authorized V0.4-B2-A) =====
# validate_core accepts a materialized Core snapshot directory and performs
# structural privacy/ownership boundary enforcement. It is deterministic and
# offline: GitHub state is never consulted. It is NOT complete secret
# detection; only structurally detectable credential/secrets cases fail closed.

CORE_SCHEMA="0.4"
CORE_ALLOWED_TOP={"config","protocol","domains","scripts","tests","docs",".github"}
CORE_ALLOWED_FILES={"README.md","AGENTS.md","requirements-dev.txt",".gitignore","LICENSE"}
CORE_PROHIBITED_TOP={"learner":"learner state","evidence":"evidence","topics":"topic execution state","execution":"execution state","coordination":"coordination state","runtime":"runtime state incl. private runtime/lineages control"}
CORE_CONFIG_FORBIDDEN={"project.yaml":"instance-authoritative project configuration","project-instructions.md":"instance-local instructions","project-ui-bootstrap.md":"instance-local UI bootstrap","deployment.yaml":"control-plane deployment binding","instance.yaml":"instance plane configuration"}
CORE_INSTANCE_DOC_TYPES={"lineage_control","branch_runtime","learning_handoff","conversation_sequence_registry","project_config","deployment_binding","migration_transaction","evidence","learner_background","learner_model","learner_calibration","learner_costs","learner_execution","learner_knowledge","topic_goal","topic_plan","topic_progress","topic_deferred","subtopic_definition","subtopic_plan","subtopic_progress","weekly_execution","daily_execution","execution_session","branch_registry","branch_report","coordination_event","hub_runtime","topic_report"}
CORE_PROHIBITED_KEYS={"canonical_status","deployment_status","deployment_epoch","active_deployment","write_state","active_deployed_commit","deployed_commit","deployed_core_commit","deployment_binding","migration_authorized","migration_authorization","active_generation","pending_handoff","lineage_control","repository_id","repository_full_name","instance_repository_id","instance_repository_full_name","core_repository_id","secret","secrets","token","tokens","password","api_key","api_token","private_key","credential","credentials"}
CORE_TOKEN_RE=re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b")
CORE_FIXTURE_DIR="tests/fixtures"

class CoreValidator:
    """Deterministic validate_core(core_snapshot) surface for the V0.4 Core plane."""
    def __init__(self,root:Path): self.root=root.resolve(); self.findings=[]; self.yaml_docs={}; self.text_docs=[]
    def error(self,c,p,m): self.findings.append(Finding("error",c,p,m))
    def run(self):
        if not self.root.is_dir(): self.error("core.snapshot",".","core snapshot root is not a directory"); return self.findings
        self.scan_top(); self.collect(); self.check_documents(); self.check_core_config(); return self.findings
    def scan_top(self):
        for e in sorted(self.root.iterdir()):
            n=e.name
            if n==".git": continue
            if e.is_dir():
                if n in CORE_PROHIBITED_TOP:
                    code="core.private_lineage" if n=="runtime" else "core.plane"
                    self.error(code,n,f"Core snapshot must not contain {n}/ ({CORE_PROHIBITED_TOP[n]} belongs to Instance/Control planes)")
                elif n not in CORE_ALLOWED_TOP: self.error("core.top_level",n,f"unexpected top-level directory {n}/")
                if n=="config":
                    for f in sorted(e.iterdir()):
                        if f.name in CORE_CONFIG_FORBIDDEN: self.error("core.config_forbidden",f"config/{f.name}",CORE_CONFIG_FORBIDDEN[f.name])
                        elif f.name!="core.yaml": self.error("core.config_entry",f"config/{f.name}","only config/core.yaml is allowed in the Core config directory")
            elif n not in CORE_ALLOWED_FILES: self.error("core.top_level",n,f"unexpected top-level file {n}")
    def collect(self):
        for f in sorted(self.root.rglob("*.y*ml")):
            rp=f.relative_to(self.root); p=rp.as_posix()
            if rp.parts[0]==".git": continue
            try: d=yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception as e: self.yaml_docs[p]=None; self.error("core.yaml_parse",p,str(e)); continue
            if not isinstance(d,dict): self.yaml_docs[p]=None; self.error("core.yaml_mapping",p,"Core YAML must be a mapping")
            else: self.yaml_docs[p]=d
        # Markdown documents are scanned for credential material at text level.
        # tests/ may legitimately contain token patterns as test material.
        for f in sorted(self.root.rglob("*.md")):
            rp=f.relative_to(self.root)
            if rp.parts[0] in (".git","tests"): continue
            self.text_docs.append(f)
    def check_keys(self,p,d,fixture=False):
        def walk(x,path):
            if isinstance(x,dict):
                for k,v in x.items():
                    kp=f"{path}.{k}" if path else str(k)
                    if not fixture and k in CORE_PROHIBITED_KEYS: self.error("core.prohibited_key",f"{p}:{kp}",f"forbidden ownership/authority key: {k}")
                    walk(v,kp)
            elif isinstance(x,list):
                for i,v in enumerate(x): walk(v,f"{path}[{i}]")
            elif isinstance(x,str) and CORE_TOKEN_RE.search(x):
                self.error("core.credential_value",f"{p}:{path}","structurally detected credential/token value")
        walk(d,"")
    def check_documents(self):
        for p,d in self.yaml_docs.items():
            if d is None: continue
            # Synthetic fixtures and .github platform workflows are exempt
            # from document-type/ownership-key checks; credential values are
            # still rejected everywhere.
            fixture=p.startswith(CORE_FIXTURE_DIR+"/") or p.startswith(".github/")
            if fixture: self.check_keys(p,d,fixture=True); continue
            t=d.get("document_type")
            if t is None: self.error("core.document_type",p,"missing document_type")
            elif t in CORE_INSTANCE_DOC_TYPES: self.error("core.plane_document",p,f"{t} is Instance/Control plane material")
            self.check_keys(p,d)
        for f in self.text_docs:
            if CORE_TOKEN_RE.search(f.read_text(encoding="utf-8")):
                self.error("core.credential_value",f.relative_to(self.root).as_posix(),"structurally detected credential/token value in document text")
    def check_core_config(self):
        p="config/core.yaml"
        if p not in self.yaml_docs: self.error("core.config_missing",p,"config/core.yaml is required (fail closed)"); return
        d=self.yaml_docs[p]
        if d is None: return
        if d.get("schema_version")!=CORE_SCHEMA: self.error("core.schema_version",p,f"core contract requires schema_version {CORE_SCHEMA!r}")
        if d.get("document_type")!="core_config": self.error("core.document_type",p,"requires document_type core_config")
        prod=d.get("product") or {}
        if not (isinstance(prod.get("id"),str) and prod["id"].strip()): self.error("core.product_identity",p,"product.id must be a non-empty string")
        m=d.get("manifest") or {}
        if not (isinstance(m.get("release"),str) and m["release"].strip()): self.error("core.manifest_release",p,"manifest.release must be a non-empty string")
        sv=m.get("supported_instance_state_schema_versions")
        if not (isinstance(sv,list) and sv and all(isinstance(x,str) and x for x in sv)): self.error("core.instance_schema_support",p,"manifest.supported_instance_state_schema_versions must be a non-empty string list")
        write_policy=m.get("runtime_session_write_policy_fingerprint")
        if not (
            isinstance(write_policy,str)
            and re.fullmatch(r"[0-9a-f]{64}",write_policy)
        ):
            self.error(
                "core.runtime_session_write_policy",
                p,
                "manifest.runtime_session_write_policy_fingerprint must be a "
                "64-hex semantic fingerprint",
            )
        for field in ("canonical_status","deployment_status"):
            if field in m:
                self.error("core.deployment_authority",p,f"manifest.{field} is forbidden: deployment authority belongs exclusively to Runtime-Control")
        t=d.get("time") or {}
        if t.get("timestamp_format")!="iso8601": self.error("core.timestamp_format",p,"time.timestamp_format must be iso8601")
        if t.get("require_reliable_source") is not True: self.error("core.reliable_time",p,"time.require_reliable_source must be true")
        proto=d.get("protocol") or {}
        routed={v for v in proto.values() if isinstance(v,str)}
        for k,v in proto.items():
            _,reason,message=resolve_repository_relative_file(self.root,v)
            if reason is not None: self.error("core.protocol_route",p,f"protocol.{k} -> invalid repository-relative file {v!r}: {reason}: {message}")
        pdir=self.root/"protocol"
        if pdir.is_dir():
            for fmd in sorted(pdir.glob("*.md")):
                if f"protocol/{fmd.name}" not in routed: self.error("core.protocol_orphan",f"protocol/{fmd.name}","protocol document is not routed by config/core.yaml")
        g=(d.get("governance") or {}).get("core_mutation") or {}
        if g.get("model")!="pull_request_required": self.error("core.governance_model",p,"governance.core_mutation.model must be pull_request_required")
        dom=d.get("domains") or {}; bases=dom.get("reusable_bases")
        if not (isinstance(bases,list) and bases and all(isinstance(x,str) and x for x in bases)): self.error("core.domains_declared",p,"domains.reusable_bases must be a non-empty string list"); bases=[]
        ddir=self.root/"domains"; actual={x.name for x in ddir.iterdir() if x.is_dir()} if ddir.is_dir() else set()
        for extra in sorted(actual-set(bases)): self.error("core.domain_undeclared",f"domains/{extra}","domain base present in tree but not declared by config/core.yaml")
        for base in bases:
            if base not in actual: self.error("core.domain_declared_missing",f"domains/{base}","declared reusable base has no directory")
        self.check_domain_bases(sorted(set(bases)&actual))
        r=self.root/"README.md"
        if not r.is_file(): self.error("core.readme","README.md","Core README is required")
    def check_domain_bases(self,bases):
        for base in bases:
            p=f"domains/{base}/curriculum.yaml"; d=self.yaml_docs.get(p)
            if d is None: self.error("core.domain_document",p,"reusable base must contain a valid curriculum.yaml"); continue
            if d.get("document_type")!="curriculum": self.error("core.domain_document",p,"requires document_type curriculum"); continue
            if (d.get("domain") or {}).get("id")!=base: self.error("core.domain_identity",p,f"domain.id must be {base!r}")
            for k,x in (d.get("nodes") or {}).items():
                if isinstance(x,dict) and x.get("kind") not in NKIND: self.error("core.curriculum_enum",p,f"nodes.{k}.kind={x.get('kind')!r} not in {sorted(NKIND)}")
            for x in d.get("edges") or []:
                if isinstance(x,dict):
                    if x.get("relation") not in EREL: self.error("core.curriculum_enum",p,f"edge {x.get('id')!r} relation invalid")
                    if x.get("strength") not in ESTR: self.error("core.curriculum_enum",p,f"edge {x.get('id')!r} strength invalid")

def validate_core(core_snapshot):
    """V0.4 Core plane validation surface: validate_core(core_snapshot).

    Accepts a materialized Core snapshot path or RepositorySnapshot.
    Deterministic and offline; GitHub state is never consulted. Provides
    structural privacy/ownership boundary enforcement, not complete secret
    detection.
    """
    # 注意：pathlib.Path 自带 .root 属性（值为 "/"），必须用显式类型判断
    # 而非 getattr 来识别 RepositorySnapshot，否则会把文件系统根当成快照。
    return CoreValidator(Path(core_snapshot.root) if isinstance(core_snapshot,RepositorySnapshot) else Path(core_snapshot)).run()

# ===== V0.4 Instance plane surface (authorized V0.4-B2-B; binding terminology
# unified in V0.4-B2-C) =====
# validate_instance(instance_snapshot, deployed_core, deployment_binding) is
# the split-aware Instance validation surface. It is deterministic and
# offline: GitHub state is never consulted. deployed_core is a locally
# materialized Core snapshot (exact pinned commit checkout);
# deployment_binding is explicit trusted metadata standing in for the
# deployment binding (synthetic fixtures, or a projection of a validated
# Runtime-Control deployment contract via validate_deployment). Live epoch
# enforcement, write_state routing, GitHub ID resolution, bootstrap locator
# resolution and name->ID verification remain runtime/resolver surfaces and
# are NOT implemented here.

INSTANCE_SCHEMA="0.4"
INSTANCE_ALLOWED_TOP={"config","learner","topics","evidence","execution","runtime","curriculum","coordination",".github"}
INSTANCE_ALLOWED_FILES={"README.md",".gitignore","LICENSE"}
# Core-owned material is forbidden in an Instance snapshot. R2B explicitly
# assigns the narrow coordination/hub/runtime.yaml family to the Instance,
# so coordination is no longer rejected as an entire top-level plane.
INSTANCE_CORE_TOP={"protocol":"Core-owned reusable protocol","scripts":"Core-owned validator implementation","tests":"Core-owned tests and fixtures","domains":"Core-owned reusable domain bases","docs":"Core/handoff/acceptance documentation"}
INSTANCE_CONFIG_FORBIDDEN={"core.yaml":"Core plane contract","project.yaml":"legacy instance-authoritative project configuration","project-instructions.md":"instance-local legacy instructions","project-ui-bootstrap.md":"instance-local legacy UI bootstrap","deployment.yaml":"Runtime-Control deployment binding (B2-C)"}
# Instance 中被拒绝的 Instance/Control 之外的 plane 文档类型
INSTANCE_FORBIDDEN_DOC_TYPES={"lineage_control":"private project-design Control lineage","project_config":"legacy canonical project configuration","deployment_binding":"Runtime-Control deployment binding","migration_transaction":"Control migration transaction","core_config":"Core plane contract document"}
INSTANCE_STATE_TYPES={"learner_background","learner_model","learner_calibration","learner_costs","learner_execution","learner_knowledge","topic_goal","topic_plan","topic_progress","topic_deferred","subtopic_definition","subtopic_plan","subtopic_progress","evidence","weekly_execution","daily_execution","execution_session","branch_registry","branch_runtime","learning_handoff","topic_report","branch_report","hub_runtime","coordination_event","conversation_sequence_registry"}
INSTANCE_REVISIONED_TYPES={"learner_execution","learner_knowledge","topic_goal","topic_plan","topic_progress","subtopic_plan","subtopic_progress","weekly_execution","daily_execution","branch_registry","branch_runtime","branch_report","hub_runtime","topic_report"}
INSTANCE_GENERIC_WRITE_VERSION_RULES={
    **{
        document_type:{"field":"revision","ordering":"positive_int"}
        for document_type in INSTANCE_REVISIONED_TYPES
    },
    "curriculum_extension":{
        "field":"extension_revision",
        "ordering":"positive_int",
    },
}
INSTANCE_CONTRACT_TYPES={"instance_config","curriculum_extension"}
INSTANCE_CURRICULUM_TYPES={"curriculum"}
# V0.4-R2B protocol/schema.md §2.1: complete split Instance physical registry.
# Rule order has no semantic meaning; callers collect every full match.
_INSTANCE_HANDOFF_FILE=r"C[^/]+-to-C[^/]+\.yaml"
INSTANCE_CANONICAL_PATH_RULES=(
    (re.compile(r"config/instance\.yaml"),"instance_config"),
    (re.compile(rf"curriculum/extensions/{_YAML_SEG}"),"curriculum_extension"),
    (re.compile(rf"curriculum/local/{_SEG}/curriculum\.yaml"),"curriculum"),
    (re.compile(r"runtime/ui/conversation-sequences\.yaml"),"conversation_sequence_registry"),
    (re.compile(r"learner/background\.yaml"),"learner_background"),
    (re.compile(r"learner/model\.yaml"),"learner_model"),
    (re.compile(r"learner/calibration\.yaml"),"learner_calibration"),
    (re.compile(r"learner/costs\.yaml"),"learner_costs"),
    (re.compile(r"learner/execution\.yaml"),"learner_execution"),
    (re.compile(rf"learner/knowledge/{_YAML_SEG}"),"learner_knowledge"),
    (re.compile(rf"topics/{_SEG}/goal\.yaml"),"topic_goal"),
    (re.compile(rf"topics/{_SEG}/plan\.yaml"),"topic_plan"),
    (re.compile(rf"topics/{_SEG}/progress\.yaml"),"topic_progress"),
    (re.compile(rf"topics/{_SEG}/deferred\.yaml"),"topic_deferred"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/definition\.yaml"),"subtopic_definition"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/plan\.yaml"),"subtopic_plan"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/progress\.yaml"),"subtopic_progress"),
    (re.compile(rf"execution/weekly/{_YAML_SEG}"),"weekly_execution"),
    (re.compile(rf"topics/{_SEG}/execution/daily/{_YAML_SEG}"),"daily_execution"),
    (re.compile(rf"topics/{_SEG}/execution/sessions/{_YAML_SEG}"),"execution_session"),
    (re.compile(rf"topics/{_SEG}/coordination/branches\.yaml"),"branch_registry"),
    (re.compile(rf"topics/{_SEG}/coordination/branches/{_SEG}/runtime\.yaml"),"branch_runtime"),
    (re.compile(rf"topics/{_SEG}/coordination/branches/{_SEG}/report\.yaml"),"branch_report"),
    (re.compile(rf"topics/{_SEG}/coordination/events/{_YAML_SEG}"),"coordination_event"),
    (re.compile(rf"topics/{_SEG}/coordination/topic-report\.yaml"),"topic_report"),
    (re.compile(r"coordination/hub/runtime\.yaml"),"hub_runtime"),
    (re.compile(rf"topics/{_SEG}/handoffs/{_SEG}/{_INSTANCE_HANDOFF_FILE}"),"learning_handoff"),
    (re.compile(rf"topics/{_SEG}/subtopics/{_SEG}/handoffs/{_SEG}/{_INSTANCE_HANDOFF_FILE}"),"learning_handoff"),
    (re.compile(rf"evidence/{_YAML_SEG}"),"evidence"),
)
INSTANCE_ALL_TYPES=frozenset(t for _,t in INSTANCE_CANONICAL_PATH_RULES)
INSTANCE_GENERIC_WRITE_MODE_OVERRIDES={
    "branch_runtime":"branch_authority",
    "hub_runtime":"hub_authority",
    "learner_knowledge":"knowledge_transition",
    "evidence":"immutable_create_only",
    "execution_session":"immutable_create_only",
    "coordination_event":"immutable_create_only",
    "learning_handoff":"immutable_create_only",
    "branch_registry":"protocol_transition",
    "conversation_sequence_registry":"protocol_transition",
    "curriculum":"protocol_transition",
    "topic_plan":"hub_transition",
    "topic_progress":"hub_transition",
    "topic_deferred":"hub_transition",
    "subtopic_definition":"hub_transition",
    "subtopic_plan":"hub_transition",
    "weekly_execution":"hub_transition",
    "topic_report":"hub_transition",
}
INSTANCE_GENERIC_WRITE_ROLE_RULES={
    "subtopic_progress": {
        "roles": ("main",),
        "scope": "bound_subtopic",
    },
    "branch_report": {
        "roles": ("hub", "main", "practice", "deep_dive"),
        "scope": "bound_branch",
    },
}
INSTANCE_GENERIC_WRITE_TRANSITION_RULES={
    "daily_execution":"preserve_locked_daily_baseline_v1",
}
INSTANCE_DEDICATED_RUNTIME_OPERATIONS={
    "create_evidence":"v1",
    "reconcile_knowledge":"v2",
    "save_learning_checkpoint":"v1",
}

def instance_expected_types(p,rules=None):
    """Return every split Instance document type whose canonical family matches p."""
    if rules is None: rules=INSTANCE_CANONICAL_PATH_RULES
    return tuple(t for rx,t in rules if rx.fullmatch(p))

def _semantic_id(value):
    if isinstance(value,dict):
        return value.get("id")
    return value

def instance_path_identity_mismatches(p,d,t):
    """Return canonical path/document identity mismatches for one Instance doc."""
    parts=PurePosixPath(p).parts
    out=[]
    def require(value,expected,label):
        actual=_semantic_id(value)
        if actual!=expected:
            out.append(f"{label} {actual!r} != path identity {expected!r}")
    def optional(value,expected,label):
        if value is not None:
            require(value,expected,label)

    if t=="curriculum":
        require(d.get("domain"),parts[2],"domain")
    elif t=="learner_knowledge":
        require(d.get("domain"),PurePosixPath(p).stem,"domain")
    elif t=="topic_goal":
        require(d.get("topic"),parts[1],"topic")
    elif t in {"topic_plan","topic_progress"}:
        require(d.get("topic"),parts[1],"topic")
    elif t=="topic_deferred":
        optional(d.get("topic"),parts[1],"topic")
    elif t=="subtopic_definition":
        require(d.get("subtopic"),parts[3],"subtopic")
    elif t in {"subtopic_plan","subtopic_progress"}:
        require(d.get("topic"),parts[1],"topic")
        require(d.get("subtopic"),parts[3],"subtopic")
    elif t=="daily_execution":
        optional(d.get("topic"),parts[1],"topic")
    elif t=="execution_session":
        require(d.get("topic"),parts[1],"topic")
        require(d.get("id"),PurePosixPath(p).stem,"session id")
    elif t=="branch_registry":
        require(d.get("topic"),parts[1],"topic")
    elif t=="branch_runtime":
        require(d.get("topic"),parts[1],"topic")
        require(d.get("branch_id"),parts[4],"branch_id")
    elif t=="branch_report":
        optional(d.get("topic"),parts[1],"topic")
        optional(d.get("branch_id"),parts[4],"branch_id")
    elif t=="coordination_event":
        optional(d.get("id"),PurePosixPath(p).stem,"event id")
    elif t=="topic_report":
        optional(d.get("topic"),parts[1],"topic")
    elif t=="learning_handoff":
        require(d.get("topic"),parts[1],"topic")
        require(d.get("lineage_id"),parts[-2],"lineage_id")
    elif t=="evidence":
        require(d.get("id"),PurePosixPath(p).stem,"evidence id")
    return out

def instance_generic_write_mode(document_type):
    """Return the generic Runtime mutation mode owned by the deployed Core."""
    if document_type not in INSTANCE_ALL_TYPES:
        return None
    return INSTANCE_GENERIC_WRITE_MODE_OVERRIDES.get(document_type,"replace")

def instance_generic_write_role_rule(document_type):
    """Return an optional canonical Branch-role constraint for generic replacement."""
    return INSTANCE_GENERIC_WRITE_ROLE_RULES.get(document_type)

def instance_generic_write_version_rule(document_type):
    """Return the semantic-version transition rule for generic replacement."""
    return INSTANCE_GENERIC_WRITE_VERSION_RULES.get(document_type)

def instance_generic_write_transition_rule(document_type):
    """Return a non-version old-to-candidate transition rule, if any."""
    return INSTANCE_GENERIC_WRITE_TRANSITION_RULES.get(document_type)

def instance_write_policy_fingerprint():
    """Stable semantic fingerprint for Runtime-session mutation semantics."""
    payload={
        "schema":"learning-os-runtime-session-write-policy-v1",
        "path_rules":[
            {"pattern":rx.pattern,"document_type":t}
            for rx,t in INSTANCE_CANONICAL_PATH_RULES
        ],
        "modes":{
            t:instance_generic_write_mode(t)
            for t in sorted(INSTANCE_ALL_TYPES)
        },
        "role_rules":{
            t:{
                "roles":list(rule["roles"]),
                "scope":rule["scope"],
            }
            for t,rule in sorted(INSTANCE_GENERIC_WRITE_ROLE_RULES.items())
        },
        "semantic_version_transition":{
            "rules":{
                t:dict(rule)
                for t,rule in sorted(
                    INSTANCE_GENERIC_WRITE_VERSION_RULES.items()
                )
            },
            "rule":"candidate_semantic_version_must_exceed_current",
        },
        "document_transition_rules":{
            t:rule
            for t,rule in sorted(
                INSTANCE_GENERIC_WRITE_TRANSITION_RULES.items()
            )
        },
        "dedicated_operations":{
            name:version
            for name,version in sorted(
                INSTANCE_DEDICATED_RUNTIME_OPERATIONS.items()
            )
        },
    }
    encoded=json.dumps(
        payload,sort_keys=True,separators=(",",":"),ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

# instance.yaml 不得重定义 Core 拥有的契约区块（Core semantic override 拒绝）
INSTANCE_CONFIG_CORE_SECTIONS={"chat_routing","protocol","domains","governance","bootstrap","manifest","runtime","time"}
# Control/deployment authority 与 credential 键：Instance 任何文档禁止
INSTANCE_FORBIDDEN_KEYS={"deployment_epoch","active_deployment","write_state","active_deployed_commit","deployed_commit","deployed_core_commit","deployment_binding","migration_authorized","migration_authorization","lineage_control","repository_id","repository_full_name","instance_repository_id","instance_repository_full_name","core_repository_id","runtime_control_repository_id","control_locator","bootstrap_locator","secret","secrets","token","tokens","password","api_key","api_token","private_key","credential","credentials"}
# 学习 lineage 字段仅允许出现在 branch_runtime（学习 lineage 归 Instance；project-design lineage 归 Control）
INSTANCE_LINEAGE_KEYS={"active_generation","pending_handoff"}
# Instance 内部物理引用只允许落在 Instance 自有 plane 内
INSTANCE_REF_PREFIXES=("learner/","topics/","evidence/","execution/","curriculum/","runtime/ui/")
# 唯一的 binding schema（B2-B 定义；B2-C 仅归一术语，不改键集）。
# context_type 键仅属于 synthetic fixture 形态；contract 投影形态不带该键。
DEPLOYMENT_BINDING_KEYS={"context_type","core_repository_id","core_commit","instance_repository_id","topology","epoch","write_state"}

class RepositorySnapshot:
    """Materialized repository tree + TRUSTED resolver-output provenance.

    repository_id / commit_sha 只能来自调用方显式提供的 trusted resolver
    output；validator 永不从被验证仓库内容读取自声明身份（self-declared
    identity 不构成信任来源）。缺失/非法 trusted metadata 一律 fail closed
    （构造即抛错，调用面转为 Finding）。
    """
    __slots__=("root","repository_id","commit_sha")
    def __init__(self,root,repository_id,commit_sha=None):
        self.root=Path(root).resolve()
        if not isinstance(repository_id,int) or isinstance(repository_id,bool) or repository_id<=0:
            raise ValueError("RepositorySnapshot.repository_id must be a positive integer (trusted provenance; fail closed)")
        if commit_sha is not None and not (isinstance(commit_sha,str) and re.fullmatch(r"[0-9a-f]{40}",commit_sha)):
            raise ValueError("RepositorySnapshot.commit_sha must be a full 40-hex commit or None (fail closed)")
        self.repository_id=repository_id; self.commit_sha=commit_sha

class DeploymentBinding:
    """显式 trusted deployment binding（B2-C 归一 B2-B trusted_context 术语）。

    form="synthetic"：B2-B 唯一定义的 fixture 形态（dict 或 YAML path，
    完整 7 键 schema，context_type 必须为 synthetic）。
    form="contract"：由已通过 DeploymentValidator 结构校验的 Runtime-Control
    deployment contract + trusted locator 投影而来；不引入第二 schema，
    不复制 deployment authority（contract 校验唯一归 DeploymentValidator）。
    字段格式的最终校验统一在 InstanceValidator.load_binding（单一权威点）。
    """
    __slots__=("form","fields","source")
    def __init__(self,source):
        # source: dict | YAML path（synthetic 形态）
        self.form="synthetic"; self.source=source; self.fields=None
    @classmethod
    def from_contract(cls,contract,locator):
        b=cls.__new__(cls)
        b.form="contract"; b.source=None
        b.fields={
            "core_repository_id":contract["core"]["repository_id"],
            "core_commit":contract["core"]["commit"],
            "instance_repository_id":locator["instance"]["repository_id"],
            "topology":contract["deployment"]["topology"],
            "epoch":contract["deployment"]["epoch"],
            "write_state":contract["deployment"]["write_state"],
        }
        return b

def validate_instance_document_trust_boundary(p,d,t):
    """Return canonical Instance authority/credential findings for one document."""
    findings=[]

    def error(code,path,message):
        findings.append(Finding("error",code,path,message))

    def walk(x,path):
        if isinstance(x,dict):
            for k,v in x.items():
                kp=f"{path}.{k}" if path else str(k)
                if k in INSTANCE_FORBIDDEN_KEYS:
                    error(
                        "instance.authority_key",
                        f"{p}:{kp}",
                        f"forbidden Control/deployment/credential key: {k}",
                    )
                elif k in INSTANCE_LINEAGE_KEYS and t!="branch_runtime":
                    error(
                        "instance.lineage_key",
                        f"{p}:{kp}",
                        f"lineage authority key {k} is only allowed in "
                        "branch_runtime (learning lineage)",
                    )
                walk(v,kp)
        elif isinstance(x,list):
            for i,v in enumerate(x):
                walk(v,f"{path}[{i}]")
        elif isinstance(x,str) and CORE_TOKEN_RE.search(x):
            error(
                "instance.credential_value",
                f"{p}:{path}",
                "structurally detected credential/token value",
            )

    walk(d,"")
    return findings


class InstanceValidator:
    """Deterministic validate_instance(instance_snapshot, deployed_core,
    deployment_binding) surface for the V0.4 Instance plane."""
    def __init__(self,root:Path,core_root,deployment_binding):
        self.root=root.resolve(); self.core_root=Path(core_root).resolve() if core_root is not None else None
        self.deployment_binding=deployment_binding
        self.findings=[]; self.docs={}; self.evidence=set(); self.curricula={}
        self.core_bases={}; self.extensions={}; self.supported_state_schemas=None; self.core_product=None
    def error(self,c,p,m): self.findings.append(Finding("error",c,p,m))
    def enum(self,p,f,v,a):
        if v not in a: self.error("enum.invalid",p,f"{f}={v!r} not in {sorted(a)}")
    def run(self):
        if not self.root.is_dir(): self.error("instance.snapshot",".","instance snapshot root is not a directory"); return self.findings
        self.scan_top(); self.collect(); self.load_binding(); self.load_core()
        self.check_documents(); self.build_curricula(); self.check_instance_config()
        self.structural(); self.refs(); self.sequence(); self.authority(); self.weekly(); self.provenance()
        return self.findings
    @staticmethod
    def instance_expected_types(p):
        return instance_expected_types(p)
    @staticmethod
    def instance_expected(p):
        matches=instance_expected_types(p)
        return matches[0] if len(matches)==1 else None
    def scan_top(self):
        for e in sorted(self.root.iterdir()):
            n=e.name
            if n==".git": continue
            if e.is_dir():
                if n in INSTANCE_CORE_TOP: self.error("instance.core_owned_top",n,f"Instance snapshot must not contain {n}/ ({INSTANCE_CORE_TOP[n]} belongs to the Core plane)")
                elif n not in INSTANCE_ALLOWED_TOP: self.error("instance.top_level",n,f"unexpected top-level directory {n}/")
                if n=="config":
                    for f in sorted(e.iterdir()):
                        if f.name in INSTANCE_CONFIG_FORBIDDEN: self.error("instance.config_forbidden",f"config/{f.name}",INSTANCE_CONFIG_FORBIDDEN[f.name])
                        elif f.name!="instance.yaml": self.error("instance.config_entry",f"config/{f.name}","only config/instance.yaml is allowed in the Instance config directory")
                if n=="runtime":
                    for sub in sorted(e.iterdir()):
                        if sub.name=="lineages": self.error("instance.private_lineage","runtime/lineages","private project-design lineage control material belongs to the Control plane, never Instance")
                        elif sub.name!="ui": self.error("instance.runtime_entry",f"runtime/{sub.name}","only runtime/ui/ is allowed under the Instance runtime directory")
            elif n not in INSTANCE_ALLOWED_FILES: self.error("instance.top_level",n,f"unexpected top-level file {n}")
    def collect(self):
        for f in sorted(self.root.rglob("*.yaml")):
            rp=f.relative_to(self.root)
            if not rp.parts or rp.parts[0] in (".git",".github"): continue
            p=rp.as_posix()
            try:d=yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception as e:self.error("yaml.parse",p,str(e));continue
            if not isinstance(d,dict):self.error("yaml.mapping",p,"canonical YAML must be mapping");continue
            self.docs[p]=d
            if d.get("document_type")=="evidence" and isinstance(d.get("id"),str):
                if d["id"] in self.evidence:self.error("evidence.duplicate_id",p,d["id"])
                self.evidence.add(d["id"])
    def load_binding(self):
        # 显式 trusted deployment binding：synthetic 形态（唯一 fixture
        # schema，未知键一律拒绝）或 contract 投影形态（键集由
        # DeploymentValidator 校验，此处仅复核字段格式）。fail closed。
        b=self.deployment_binding
        if b is None: self.error("instance.deployment_binding","<deployment-binding>","trusted deployment binding is required (fail closed)"); return
        if isinstance(b,DeploymentBinding):
            if b.form=="contract" and isinstance(b.fields,dict): d=b.fields; skip_synthetic_keys=True
            elif b.form=="synthetic":
                src=b.source; d=None; skip_synthetic_keys=False
                if isinstance(src,dict): d=src
                else:
                    p=Path(src)
                    if not p.is_file(): self.error("instance.deployment_binding",str(src),"deployment binding file is missing (fail closed)"); return
                    try:d=yaml.safe_load(p.read_text(encoding="utf-8"))
                    except Exception as e:self.error("instance.deployment_binding",str(src),str(e)); return
            else: self.error("instance.deployment_binding","<deployment-binding>","unsupported binding form"); return
        else:
            # 兼容直接传 dict / YAML path 的调用方式（视作 synthetic 形态）
            d=b; skip_synthetic_keys=False
            if not isinstance(d,dict):
                p=Path(d)
                if not p.is_file(): self.error("instance.deployment_binding",str(d),"deployment binding file is missing (fail closed)"); return
                try:d=yaml.safe_load(p.read_text(encoding="utf-8"))
                except Exception as e:self.error("instance.deployment_binding",str(d),str(e)); return
        if not isinstance(d,dict): self.error("instance.deployment_binding","<deployment-binding>","deployment binding must be a mapping"); return
        if not skip_synthetic_keys:
            unknown=sorted(set(d)-DEPLOYMENT_BINDING_KEYS)
            if unknown: self.error("instance.deployment_binding_keys","<deployment-binding>",f"unknown deployment binding keys {unknown}; the binding schema must be extended explicitly")
            for k in sorted(DEPLOYMENT_BINDING_KEYS):
                if k not in d: self.error("instance.deployment_binding_missing",f"<deployment-binding>:{k}","missing required deployment binding key")
            if d.get("context_type")!="synthetic": self.error("instance.deployment_binding_type","<deployment-binding>:context_type","only synthetic deployment bindings are valid fixtures; live bindings are projections of a validated deployment contract (validate_deployment)")
        for k in ("core_repository_id","instance_repository_id"):
            v=d.get(k)
            if not isinstance(v,int) or isinstance(v,bool) or v<=0: self.error("instance.deployment_binding_id",f"<deployment-binding>:{k}","must be a positive integer repository ID")
        cc=d.get("core_commit")
        if not (isinstance(cc,str) and re.fullmatch(r"[0-9a-f]{40}",cc)): self.error("instance.deployment_binding_commit","<deployment-binding>:core_commit","must be a 40-hex core commit")
        for k in ("topology","write_state"):
            v=d.get(k)
            if not isinstance(v,str) or not v.strip(): self.error("instance.deployment_binding_field",f"<deployment-binding>:{k}","must be a non-empty string")
        ep=d.get("epoch")
        if not isinstance(ep,int) or isinstance(ep,bool) or ep<1: self.error("instance.deployment_binding_epoch","<deployment-binding>:epoch","must be a positive integer")
    def load_core(self):
        # deployed_core 是本地物化的 Core 快照（trusted context pin 的 exact commit 检出）。
        # 仅从快照读取 manifest 与 domain bases；不做 GitHub 在线发现。
        if self.core_root is None or not self.core_root.is_dir(): self.error("instance.core_snapshot","<core-snapshot>","deployed Core snapshot is required (fail closed)"); return
        cc=self.core_root/"config/core.yaml"
        if not cc.is_file(): self.error("instance.core_config","config/core.yaml","Core snapshot is missing config/core.yaml (fail closed)"); return
        try:d=yaml.safe_load(cc.read_text(encoding="utf-8"))
        except Exception as e:self.error("instance.core_config","config/core.yaml",str(e)); return
        if not isinstance(d,dict): self.error("instance.core_config","config/core.yaml","Core config must be a mapping"); return
        self.core_product=(d.get("product") or {}).get("id") if isinstance(d.get("product"),dict) else None
        m=d.get("manifest") or {}
        sv=m.get("supported_instance_state_schema_versions") if isinstance(m,dict) else None
        if isinstance(sv,list) and all(isinstance(x,str) and x for x in sv): self.supported_state_schemas=set(sv)
        else: self.error("instance.core_manifest","config/core.yaml","Core manifest.supported_instance_state_schema_versions missing or invalid (fail closed)")
        ddir=self.core_root/"domains"
        if ddir.is_dir():
            for f in sorted(ddir.glob("*/curriculum.yaml")):
                try:c=yaml.safe_load(f.read_text(encoding="utf-8"))
                except Exception: continue
                if isinstance(c,dict) and isinstance(c.get("domain"),dict) and isinstance(c["domain"].get("id"),str): self.core_bases[c["domain"]["id"]]=c
    def check_keys(self,p,d,t):
        self.findings.extend(
            validate_instance_document_trust_boundary(p,d,t)
        )
    def check_documents(self):
        for p,d in self.docs.items():
            t=d.get("document_type")
            if t is None: self.error("yaml.document_type",p,"missing document_type")
            elif t in INSTANCE_FORBIDDEN_DOC_TYPES: self.error("instance.forbidden_document",p,f"{INSTANCE_FORBIDDEN_DOC_TYPES.get(t,'forbidden')} is not Instance-plane material")
                # still fall through to key scan for defense in depth
            elif t not in INSTANCE_ALL_TYPES: self.error("instance.unknown_document_type",p,f"unknown Instance document type {t!r}")
            matches=instance_expected_types(p)
            if not matches: self.error("path.unregistered",p,f"no canonical split Instance storage family registered for {p}")
            elif len(matches)>1: self.error("path.ambiguous",p,f"multiple split Instance storage families match {p}: {list(matches)}")
            elif t is not None and t!=matches[0]: self.error("path.document_type",p,f"expected {matches[0]}, found {t}")
            if len(matches)==1 and t==matches[0]:
                for mismatch in instance_path_identity_mismatches(p,d,t):
                    self.error("path.identity",p,mismatch)
            if "schema_version" not in d: self.error("yaml.schema_version",p,"missing schema_version")
            elif t in INSTANCE_STATE_TYPES and self.supported_state_schemas is not None and d["schema_version"] not in self.supported_state_schemas:
                self.error("instance.state_schema_unsupported",p,f"state schema_version {d['schema_version']!r} not in Core supported_instance_state_schema_versions {sorted(self.supported_state_schemas)}")
            elif t in INSTANCE_CONTRACT_TYPES and d["schema_version"]!=INSTANCE_SCHEMA:
                self.error("instance.contract_schema",p,f"{t} is a V0.4-only contract document and requires schema_version {INSTANCE_SCHEMA!r}")
            self.check_keys(p,d,t)
        for f in sorted(self.root.rglob("*.md")):
            if CORE_TOKEN_RE.search(f.read_text(encoding="utf-8")):
                self.error("instance.credential_value",f.relative_to(self.root).as_posix(),"structurally detected credential/token value in document text")
    def build_curricula(self):
        # deterministic resolution order: Instance local domains -> Core bases
        # -> extensions merged additively (collisions fail closed)
        ldir=self.root/"curriculum/local"
        if ldir.is_dir():
            for f in sorted(ldir.glob("*/curriculum.yaml")):
                p=f.relative_to(self.root).as_posix(); d=self.docs.get(p)
                if d is None: continue
                dom=(d.get("domain") or {}).get("id")
                if not isinstance(dom,str): continue
                if dom in self.core_bases: self.error("instance.domain_shadow",p,f"local domain {dom!r} collides with a Core base domain (same ID, different content)")
                self.curricula[dom]={"nodes":d.get("nodes") or {},"aliases":d.get("aliases") or {},"version":d.get("curriculum_version")}
        for dom,c in sorted(self.core_bases.items()):
            if dom in self.curricula: continue
            self.curricula[dom]={"nodes":c.get("nodes") or {},"aliases":c.get("aliases") or {},"version":c.get("curriculum_version")}
        edir=self.root/"curriculum/extensions"
        if edir.is_dir():
            for f in sorted(edir.glob("*.yaml")):
                p=f.relative_to(self.root).as_posix(); d=self.docs.get(p)
                if d is None: continue
                self.apply_extension(p,d)
    def apply_extension(self,p,d):
        # extension 语义：additive-only。任何对 Core base 对象的重定义/删除/
        # 别名重定向/capability 覆盖均 fail closed。
        dom=d.get("domain"); base=self.core_bases.get(dom) if isinstance(dom,str) else None
        if not isinstance(dom,str) or not dom: self.error("instance.extension_domain",p,"extension requires a domain"); return
        if base is None: self.error("instance.extension_base",p,f"extension domain {dom!r} has no Core base"); return
        if d.get("base_version")!=base.get("curriculum_version"): self.error("instance.extension_base_version",p,f"base_version {d.get('base_version')!r} does not match Core base curriculum_version {base.get('curriculum_version')!r}")
        r=d.get("extension_revision")
        if not isinstance(r,int) or isinstance(r,bool) or r<1: self.error("instance.extension_revision",p,"extension_revision must be a positive integer")
        bn=base.get("nodes") or {}
        be={e.get("id") for e in base.get("edges") or [] if isinstance(e,dict)}
        ba=base.get("aliases") or {}
        bp=base.get("capability_profiles") or {}
        for k,x in (d.get("nodes") or {}).items():
            if k in bn: self.error("instance.node_collision",p,f"extension node {k!r} shadows a Core base node")
            if isinstance(x,dict) and (x.get("tombstone") or x.get("deleted")): self.error("instance.core_delete",p,f"extension node {k!r} carries a delete/tombstone marker; extensions are additive-only")
        for x in d.get("edges") or []:
            if isinstance(x,dict):
                if x.get("id") in be: self.error("instance.edge_collision",p,f"extension edge {x.get('id')!r} shadows a Core base edge")
                if x.get("tombstone") or x.get("deleted"): self.error("instance.core_delete",p,f"extension edge {x.get('id')!r} carries a delete/tombstone marker; extensions are additive-only")
        for k in (d.get("aliases") or {}):
            if k in ba: self.error("instance.alias_redirect",p,f"extension alias {k!r} redefines a Core base alias")
        for k in (d.get("capability_profiles") or {}):
            if k in bp: self.error("instance.capability_override",p,f"extension capability profile {k!r} overrides a Core base profile")
        pr=d.get("probes")
        if pr is not None and (not isinstance(pr,list) or any(not isinstance(x,dict) or not x.get("id") for x in pr)): self.error("instance.extension_probes",p,"probes must be a list of mappings with ids")
        v=self.curricula.setdefault(dom,{"nodes":{},"aliases":{},"version":base.get("curriculum_version")})
        v["nodes"].update(d.get("nodes") or {}); v["aliases"].update(d.get("aliases") or {})
        self.extensions[dom]=d
    def check_instance_config(self):
        p="config/instance.yaml"
        if p not in self.docs: self.error("instance.config_missing",p,"config/instance.yaml is required (fail closed)"); return
        d=self.docs[p]
        prod=d.get("product") or {}
        if not (isinstance(prod.get("id"),str) and prod["id"].strip()): self.error("instance.product_identity",p,"product.id must be a non-empty string")
        elif isinstance(self.core_product,str) and prod["id"]!=self.core_product: self.error("instance.product_mismatch",p,f"product.id {prod['id']!r} does not match deployed Core product.id {self.core_product!r}")
        inst=d.get("instance") or {}
        if not (isinstance(inst.get("display_timezone"),str) and inst["display_timezone"].strip()): self.error("instance.timezone",p,"instance.display_timezone must be a non-empty string")
        for k in d:
            if k in INSTANCE_CONFIG_CORE_SECTIONS: self.error("instance.core_override",f"{p}:{k}",f"{k} is Core-owned reusable semantics; instance.yaml must not override it")
        if "nonproduction" in d and not isinstance(d["nonproduction"],bool): self.error("instance.nonproduction",p,"nonproduction must be a boolean")
    def structural(self):
        req={"instance_config":("product","instance"),"curriculum_extension":("domain","base_version","extension_revision"),"conversation_sequence_registry":("sequence_format","scopes"),"learner_knowledge":("revision","domain","concepts"),"curriculum":("domain","curriculum_version","nodes"),"topic_goal":("revision","topic","goal"),"topic_plan":("revision","topic","plan"),"topic_progress":("revision","topic","plan_revision","lifecycle","milestones"),"subtopic_definition":("subtopic",),"subtopic_plan":("revision","topic","subtopic","plan"),"subtopic_progress":("revision","topic","subtopic","plan_revision","milestones"),"weekly_execution":("revision","window"),"branch_registry":("revision","topic","branches"),"branch_runtime":("revision","topic","branch_id","lineage_id","active_generation","pending_successor","generations"),"execution_session":("id","topic","branch","meaningful_learning"),"learning_handoff":("topic","branch_id","lineage_id","from_generation","to_generation"),"evidence":("id","observed_at","observation","interpretation","targets")}
        for p,d in self.docs.items():
            t=d.get("document_type")
            if t not in INSTANCE_ALL_TYPES: continue
            for k in req.get(t,()):
                if k not in d:self.error("document.required",p,f"missing {k}")
            if t in INSTANCE_REVISIONED_TYPES:
                revision=d.get("revision")
                if not isinstance(revision,int) or isinstance(revision,bool) or revision<1:
                    self.error(
                        "revision.invalid",
                        p,
                        "revisioned document requires a positive integer revision",
                    )
            if t in {"learner_execution", "topic_goal"}:
                owner = d.get("goal", {}) if t == "topic_goal" else d
                try:
                    if not isinstance(owner, dict):
                        raise ValueError("goal must be a mapping")
                    validate_intake_preferences(owner.get("preferences"))
                except ValueError as exc:
                    self.error("intake.preference", p, str(exc))
            if t=="topic_plan":self.enum(p,"plan.status",(d.get("plan")or{}).get("status"),{"awaiting_intake","provisional","active","paused"})
            elif t=="topic_progress":
                self.enum(p,"lifecycle",d.get("lifecycle"),TLIFE)
                for k,x in (d.get("milestones")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"milestones.{k}.status",x.get("status"),PLANNED)
            elif t=="subtopic_definition":s=d.get("subtopic")or{};self.enum(p,"subtopic.kind",s.get("kind"),SKIND);self.enum(p,"subtopic.lifecycle",s.get("lifecycle"),SLIFE)
            elif t=="subtopic_plan":self.enum(p,"plan.status",(d.get("plan")or{}).get("status"),{"provisional","active","paused"})
            elif t=="subtopic_progress":
                for k,x in (d.get("milestones")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"milestones.{k}.status",x.get("status"),PLANNED)
            elif t=="learner_knowledge":
                for c,x in (d.get("concepts")or{}).items():
                    for a,y in ((x or{}).get("capabilities")or{}).items():self.enum(p,f"{c}.{a}.state",y.get("state"),CAP);self.enum(p,f"{c}.{a}.confidence",y.get("confidence"),CONF)
            elif t=="evidence":
                x=d.get("interpretation")or{};self.enum(p,"interpretation.direction",x.get("direction"),EDIR)
                for k in ("diagnosticity","novelty","confidence"):self.enum(p,f"interpretation.{k}",x.get(k),CONF)
                if "classification_confidence" in x or "classification_confidence" in d:self.error("evidence.legacy_confidence",p,"use interpretation.confidence")
            elif t in ("curriculum","curriculum_extension"):
                for k,x in (d.get("nodes")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"nodes.{k}.kind",x.get("kind"),NKIND)
                for x in d.get("edges")or[]:
                    if isinstance(x,dict):self.enum(p,"edge.relation",x.get("relation"),EREL);self.enum(p,"edge.strength",x.get("strength"),ESTR)
            elif t=="branch_registry":
                for k,x in (d.get("branches")or{}).items():self.enum(p,f"branches.{k}.role",x.get("role"),BROLE);self.enum(p,f"branches.{k}.lifecycle",x.get("lifecycle"),BLIFE)
            elif t=="branch_runtime":
                for k,x in (d.get("generations")or{}).items():
                    if isinstance(x,dict):self.enum(p,f"generations.{k}.lifecycle",x.get("lifecycle"),GEN)
            elif t=="weekly_execution":
                for s in ("baseline_outcomes","current_outcomes"):
                    for x in d.get(s)or[]:
                        if isinstance(x,dict) and "status" in x:self.enum(p,f"{s}.{x.get('id')}.status",x.get("status"),PLANNED)
    def refs(self):
        # 与 legacy refs() 相同的语义检查，但 curriculum 解析针对
        # effective view（Core base + Instance extension 合并视图）。
        for p,d in self.docs.items():
            t=d.get("document_type")
            if t=="topic_progress":
                q=self.docs.get(f"topics/{d.get('topic')}/plan.yaml")
                if q and d.get("plan_revision")!=q.get("revision"):self.error("revision.topic_progress_plan",p,"plan_revision != current topic plan revision")
            elif t=="subtopic_progress":
                q=self.docs.get(f"topics/{d.get('topic')}/subtopics/{d.get('subtopic')}/plan.yaml")
                if q and d.get("plan_revision")!=q.get("revision"):self.error("revision.subtopic_progress_plan",p,"plan_revision != current subtopic plan revision")
            elif t=="topic_plan":
                q=self.docs.get(f"topics/{d.get('topic')}/goal.yaml");r=((d.get("plan")or{}).get("based_on")or{}).get("goal_revision")
                if q and isinstance(r,int) and r>q.get("revision",r):self.error("revision.topic_plan_goal_future",p,"goal_revision points to future revision")
            elif t=="subtopic_plan":
                q=self.docs.get(f"topics/{d.get('topic')}/plan.yaml");r=((d.get("plan")or{}).get("based_on")or{}).get("topic_plan_revision")
                if q and isinstance(r,int) and r>q.get("revision",r):self.error("revision.subtopic_plan_topic_future",p,"topic_plan_revision points to future revision")
                for m in (d.get("plan")or{}).get("milestones")or[]:
                    for r in (m or{}).get("curriculum_refs")or[]:
                        if not isinstance(r,dict) or r.get("type")!="curriculum_node":continue
                        c=self.curricula.get(r.get("domain"));n=r.get("id")
                        if not c:self.error("reference.curriculum_domain",p,f"missing curriculum {r.get('domain')}");continue
                        nodes,aliases=c.get("nodes")or{},c.get("aliases")or{};a=aliases.get(n)
                        if isinstance(a,dict):a=a.get("to")or a.get("target")or a.get("id")
                        if n not in nodes and (not isinstance(a,str) or a not in nodes):self.error("reference.curriculum_node",p,f"unresolved curriculum node {r.get('domain')}:{n}")
            elif t=="learner_knowledge":
                for c,x in (d.get("concepts")or{}).items():
                    for a,y in ((x or{}).get("capabilities")or{}).items():
                        for side in ("support","challenge"):
                            for r in ((y or{}).get("evidence_refs")or{}).get(side)or[]:
                                if r not in self.evidence:self.error("reference.evidence_missing",p,f"{c}.{a} -> {r}")
    def sequence(self):
        p="runtime/ui/conversation-sequences.yaml";d=self.docs.get(p)
        if not d:return
        scopes=d.get("scopes")or{};np=False
        for s,x in scopes.items():
            if not isinstance(x,dict):self.error("sequence.scope_record",p,s);continue
            n=x.get("last_allocated")
            if not isinstance(n,int) or isinstance(n,bool) or n<0:self.error("sequence.last_allocated",p,s)
            if s.startswith(("acceptance:","test:")):np=True;Validator.prod(s.split(":",1)[1]) or self.error("sequence.nonproduction_scope",p,s)
            elif not Validator.prod(s):self.error("sequence.production_scope",p,s)
        if np and (d.get("nonproduction_sequence_format")or{}).get("prefix")!="T":self.error("sequence.nonproduction_prefix",p,"non-production prefix must be T")
        w=(d.get("sequence_format")or{}).get("minimum_width",2)
        for i,r in enumerate(d.get("repair_history")or[]):
            if not isinstance(r,dict):self.error("sequence.repair_record",p,str(i));continue
            a,b=r.get("previous_last_allocated"),r.get("repaired_last_allocated")
            if not Validator.prod(str(r.get("scope"))):self.error("sequence.repair_scope",p,str(i))
            if not all(isinstance(x,int) and not isinstance(x,bool) for x in (a,b)) or not 0<=b<a:self.error("sequence.repair_range",p,str(i));continue
            if r.get("orphaned_suffix")!=[f"C{n:0{w}d}" for n in range(b+1,a+1)]:self.error("sequence.repair_suffix",p,str(i))
            if any(not r.get(k) for k in ("reason","repaired_at","authority")):self.error("sequence.repair_provenance",p,str(i))
            cur=(scopes.get(r.get("scope"))or{}).get("last_allocated")
            if isinstance(cur,int) and cur<b:self.error("sequence.repair_current",p,str(i))
    def check_instance_ref(self,p,ref,field,require_exists=True):
        # Instance 内部物理引用规则：必须相对路径、禁止 ../ 穿越、禁止绝对
        # 路径、只能落在 Instance 自有 plane 内（cross-plane 引用 fail closed）。
        if not isinstance(ref,str) or not ref.strip(): self.error("instance.ref_invalid",f"{p}:{field}",repr(ref)); return False
        if ref.startswith(("/","~")) or re.match(r"^[A-Za-z]:[\\/]",ref): self.error("instance.ref_absolute",f"{p}:{field}",ref); return False
        if ".." in ref.split("/"): self.error("instance.ref_traversal",f"{p}:{field}",ref); return False
        if not ref.startswith(INSTANCE_REF_PREFIXES): self.error("instance.ref_cross_plane",f"{p}:{field}",f"{ref} resolves outside Instance-owned planes"); return False
        if require_exists and not (self.root/ref).is_file(): self.error("instance.ref_missing",f"{p}:{field}",ref); return False
        return True
    def authority(self):
        # Instance 只校验学习 lineage（branch_runtime）；project-design
        # lineage（lineage_control）已在 check_documents 中被拒绝。
        for p,d in self.docs.items():
            if d.get("document_type")!="branch_runtime": continue
            a=d.get("active_generation");g=d.get("generations")or{};r=g.get(a,g.get(str(a))) if isinstance(a,int) else None
            if r is None:self.error("branch.active_record",p,f"missing generation {a}");continue
            act=[k for k,x in g.items() if isinstance(x,dict) and x.get("lifecycle")=="active"];h=d.get("pending_successor")
            if h is None:
                if len(act)!=1:self.error("branch.active_count",p,f"active={act}")
                if r.get("lifecycle")!="active":self.error("branch.active_lifecycle",p,"active record not active")
            else:
                n=h.get("generation") if isinstance(h,dict) else h
                if r.get("lifecycle")!="handoff_pending":self.error("branch.pending_lifecycle",p,"source not handoff_pending")
                if not isinstance(n,int) or n<=a:self.error("branch.pending_generation",p,"pending successor must advance")
            for generation_key,x in g.items():
                if not isinstance(x,dict) or "handoff_ref" not in x: continue
                ref=x.get("handoff_ref")
                where=f"{p}:generations.{generation_key}.handoff_ref"
                if not isinstance(ref,str) or not ref.strip(): self.error("instance.ref_invalid",where,repr(ref)); continue
                if ref.startswith(("/","~")) or re.match(r"^[A-Za-z]:",ref): self.error("instance.ref_absolute",where,ref); continue
                if ".." in ref.split("/"): self.error("instance.ref_traversal",where,ref); continue
                if not ref.startswith(INSTANCE_REF_PREFIXES): self.error("instance.ref_cross_plane",where,f"{ref} resolves outside Instance-owned planes"); continue
                _,reason,message=resolve_repository_relative_file(self.root,ref)
                if reason is not None:
                    self.error("branch.handoff_ref",where,f"{ref!r}: {reason}: {message}"); continue
                if expected_types_for_path(ref)!=("learning_handoff",):
                    self.error("branch.handoff_ref_target",where,f"{ref!r} is not a canonical learning_handoff path"); continue
                target=self.docs.get(ref)
                if target is None or target.get("document_type")!="learning_handoff":
                    self.error("branch.handoff_ref_target",where,f"{ref!r} does not resolve to a loaded learning_handoff mapping"); continue
                if self.supported_state_schemas is not None and target.get("schema_version") not in self.supported_state_schemas:
                    self.error("branch.handoff_ref_target",where,f"{ref!r} uses unsupported learning_handoff schema_version {target.get('schema_version')!r}"); continue
                mismatches=learning_handoff_identity_mismatches(d,generation_key,x,target)
                if mismatches:self.error("branch.handoff_ref_identity",where,"; ".join(mismatches))
    def weekly(self):
        for p,d in self.docs.items():
            if d.get("document_type")!="weekly_execution" or d.get("current_outcomes") is None or d.get("closing") is not None:continue
            x=d.get("projection")
            if not isinstance(x,dict):self.error("weekly.projection",p,"open current_outcomes require provenance");continue
            if not x.get("observed_at"):self.error("weekly.projection_observed_at",p,"missing observed_at")
            if x.get("reconciliation") not in {"read_time","explicit_reconciliation"}:self.error("weekly.projection_reconciliation",p,"invalid reconciliation")
            src=x.get("source_revisions")or[]
            if not isinstance(src,list) or not src:self.error("weekly.projection_sources",p,"missing sources");continue
            for s in src:
                if not isinstance(s,dict) or not s.get("ref") or not isinstance(s.get("revision"),int):self.error("weekly.projection_source",p,"invalid source");continue
                # source ref 必须严格落在 Instance root 内且属于 Instance plane
                if not self.check_instance_ref(p,s["ref"],"source_revisions.ref",require_exists=False):continue
                q=self.docs.get(s["ref"])
                if q is None:self.error("weekly.projection_source_missing",p,s["ref"])
                elif isinstance(q.get("revision"),int) and s["revision"]>q["revision"]:self.error("weekly.projection_source_future",p,s["ref"])
    def provenance(self):
        # Plan provenance：legacy form（curriculum_version）与 new form
        # （base_version + extension_revision）同时接受；同一条目同时出现
        # 两种字段 = ambiguous = fail closed。不迁移任何 production Plan。
        for p,d in self.docs.items():
            if d.get("document_type")!="topic_plan": continue
            cur=((d.get("plan")or{}).get("based_on")or{}).get("curricula")
            if cur is None: continue
            if not isinstance(cur,list): self.error("instance.provenance_form",p,"plan.based_on.curricula must be a list"); continue
            for i,e in enumerate(cur):
                where=f"{p}:based_on.curricula[{i}]"
                if not isinstance(e,dict): self.error("instance.provenance_form",where,"entry must be a mapping"); continue
                dom=e.get("domain"); legacy_v=e.get("curriculum_version"); base_v=e.get("base_version"); ext_r=e.get("extension_revision")
                has_legacy=legacy_v is not None; has_new=base_v is not None or ext_r is not None
                if has_legacy and has_new: self.error("instance.provenance_ambiguous",where,"entry mixes legacy curriculum_version and V0.4 base_version/extension_revision forms")
                elif has_legacy:
                    v=self.curricula.get(dom)
                    if v is None: self.error("instance.provenance_domain",where,f"unknown curriculum domain {dom!r}")
                    elif legacy_v!=v.get("version"): self.error("instance.provenance_version",where,f"curriculum_version {legacy_v!r} != effective curriculum version {v.get('version')!r}")
                elif has_new:
                    base=self.core_bases.get(dom)
                    if base is None: self.error("instance.provenance_domain",where,f"new-form provenance requires a Core base domain, got {dom!r}")
                    else:
                        if base_v!=base.get("curriculum_version"): self.error("instance.provenance_base",where,f"base_version {base_v!r} != Core base curriculum_version {base.get('curriculum_version')!r}")
                        if not isinstance(ext_r,int) or isinstance(ext_r,bool) or ext_r<1: self.error("instance.provenance_revision",where,"extension_revision must be a positive integer")
                        elif dom not in self.extensions or ext_r>self.extensions[dom].get("extension_revision",-1): self.error("instance.provenance_extension",where,f"references extension revision that does not exist for domain {dom!r}")
                else: self.error("instance.provenance_form",where,"entry carries neither curriculum_version nor base_version/extension_revision")

def validate_instance(instance_snapshot, deployed_core, deployment_binding):
    """V0.4 Instance plane validation surface:
    validate_instance(instance_snapshot, deployed_core, deployment_binding).

    instance_snapshot: materialized Instance tree path (or RepositorySnapshot).
    deployed_core: locally materialized Core snapshot path (exact pinned
        commit checkout) or RepositorySnapshot. Never "latest Core main";
        GitHub is not consulted.
    deployment_binding: explicit trusted deployment binding — a DeploymentBinding,
        dict, or YAML file path. Synthetic fixtures carry the single binding
        schema (context_type: synthetic); live bindings are projections of a
        validated deployment contract (see validate_deployment). Live epoch
        enforcement, write_state routing, ID resolution remain runtime/resolver
        surfaces and are not implemented here.

    Deterministic and offline. Fail-closed privacy/ownership boundary
    enforcement for the Instance plane, not complete secret detection.
    """
    # 显式类型判断（Path.root 是 "/"，不能用 getattr 识别 RepositorySnapshot）
    iroot=instance_snapshot.root if isinstance(instance_snapshot,RepositorySnapshot) else instance_snapshot
    croot=deployed_core.root if isinstance(deployed_core,RepositorySnapshot) else deployed_core
    return InstanceValidator(Path(iroot),croot,deployment_binding).run()

# ===== V0.4 Deployment plane surface (authorized V0.4-B2-C) =====
# validate_deployment(control_snapshot, deployed_core, instance_snapshot,
# trusted_locator) is the split deployment validation surface. Deterministic
# and offline: it never resolves repositories, never contacts GitHub, never
# fetches live deployment state and never checks platform permissions
# (no resolve_repository / lookup_github / fetch_live_deployment /
# check_platform_permissions — those belong to a future resolver/runtime
# surface). Snapshot provenance is CALLER-SUPPLIED trusted resolver output;
# self-declared identity inside any repository content is never trusted.

DEPLOYMENT_SCHEMA="0.4"
DEPLOYMENT_DOC_TYPE="deployment_binding"
# public Runtime-Control deployment contract allowlist（fail closed：allowlist
# 外任何键都拒绝；Instance identity / lineage / migration / credentials
# 属于被禁的信任边界内容，见 DEPLOYMENT_TRUST_BOUNDARY_KEYS）
DEPLOYMENT_TOP_KEYS={"schema_version","document_type","updated_at","deployment","core"}
DEPLOYMENT_SECTION_KEYS={"deployment":{"id","topology","epoch","write_state"},"core":{"repository_id","commit","repository_full_name"}}
DEPLOYMENT_REQUIRED={"deployment":{"id","topology","epoch","write_state"},"core":{"repository_id","commit"}}
DEPLOYMENT_TOPOLOGIES={"split"}
DEPLOYMENT_WRITE_STATES={"active","frozen"}
DEPLOYMENT_TRUST_BOUNDARY_KEYS={
    "instance_repository_id":"Instance identity must come from the trusted locator, never the public contract",
    "instance_repository_full_name":"Instance identity must come from the trusted locator, never the public contract",
    "instance_canonical_ref":"Instance identity must come from the trusted locator, never the public contract",
    "instance":"Instance identity block is forbidden in the public Runtime-Control contract",
    "runtime_control_repository_id":"a repository must not self-assert its own trusted identity",
    "active_generation":"lineage authority does not belong in a deployment contract",
    "pending_handoff":"lineage authority does not belong in a deployment contract",
    "handoff_id":"lineage authority does not belong in a deployment contract",
    "lineage_control":"lineage authority does not belong in a deployment contract",
    "migration_transaction":"migration transaction state belongs to private Control, never the public contract",
    "migration_authorized":"migration authorization belongs to private Control, never the public contract",
    "migration_authorization":"migration authorization belongs to private Control, never the public contract",
    "migration_state":"migration transaction state belongs to private Control, never the public contract",
    "secret":"credentials are forbidden in the public contract","secrets":"credentials are forbidden in the public contract",
    "token":"credentials are forbidden in the public contract","tokens":"credentials are forbidden in the public contract",
    "password":"credentials are forbidden in the public contract","api_key":"credentials are forbidden in the public contract",
    "api_token":"credentials are forbidden in the public contract","private_key":"credentials are forbidden in the public contract",
    "credential":"credentials are forbidden in the public contract","credentials":"credentials are forbidden in the public contract",
}
# 外部 trusted locator 契约（TRUST ROOT；navigation 字段不参与信任判断）
LOCATOR_TOP_KEYS={"schema_version","document_type","runtime_control","instance"}
LOCATOR_RC_KEYS={"repository_id","repository","canonical_ref","contract_path"}
LOCATOR_INSTANCE_KEYS={"repository_id","repository","canonical_ref"}

def validate_deployment_contract_document(
    contract,
    *,
    path="<deployment-contract>",
    raw_text=None,
    structure=True,
    trust_boundary=True,
):
    """Validate one Runtime-Control contract document without snapshot lookup."""
    findings = []

    def error(code, where, message):
        findings.append(Finding("error", code, where, message))

    if structure:
        if not isinstance(contract, dict):
            error(
                "yaml.mapping",
                path,
                "deployment contract must be a mapping",
            )
            return findings
        if contract.get("schema_version") != DEPLOYMENT_SCHEMA:
            error(
                "deployment.schema_version",
                path,
                f"deployment contract schema_version must be {DEPLOYMENT_SCHEMA!r}",
            )
        if contract.get("document_type") != DEPLOYMENT_DOC_TYPE:
            error(
                "deployment.document_type",
                path,
                "deployment contract document_type must be "
                f"{DEPLOYMENT_DOC_TYPE!r}",
            )
        unknown = sorted(set(contract) - DEPLOYMENT_TOP_KEYS)
        if unknown:
            error(
                "deployment.forbidden_field",
                path,
                "unknown top-level contract fields "
                f"{unknown}; the public contract is allowlist-only",
            )
        deployment = contract.get("deployment")
        core = contract.get("core")
        if not isinstance(deployment, dict):
            error(
                "deployment.section",
                path,
                "deployment section must be a mapping",
            )
            deployment = {}
        if not isinstance(core, dict):
            error(
                "deployment.section",
                path,
                "core section must be a mapping",
            )
            core = {}
        for section, value in (
            ("deployment", deployment),
            ("core", core),
        ):
            extra = sorted(set(value) - DEPLOYMENT_SECTION_KEYS[section])
            if extra:
                error(
                    "deployment.forbidden_field",
                    f"{path}:{section}",
                    f"unknown {section} fields {extra}; "
                    "the public contract is allowlist-only",
                )
            for field in DEPLOYMENT_REQUIRED[section]:
                if field not in value:
                    error(
                        "deployment.required_field",
                        f"{path}:{section}.{field}",
                        f"missing required field {section}.{field}",
                    )

        deployment_id = deployment.get("id")
        if not (
            isinstance(deployment_id, str)
            and deployment_id.strip()
        ):
            error(
                "deployment.id",
                f"{path}:deployment.id",
                "must be a non-empty string",
            )
        if deployment.get("topology") not in DEPLOYMENT_TOPOLOGIES:
            error(
                "deployment.topology",
                f"{path}:deployment.topology",
                f"must be one of {sorted(DEPLOYMENT_TOPOLOGIES)}",
            )
        epoch = deployment.get("epoch")
        if (
            not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or epoch < 1
        ):
            error(
                "deployment.epoch",
                f"{path}:deployment.epoch",
                "must be a positive integer (monotonic fencing token)",
            )
        if deployment.get("write_state") not in DEPLOYMENT_WRITE_STATES:
            error(
                "deployment.write_state",
                f"{path}:deployment.write_state",
                f"must be one of {sorted(DEPLOYMENT_WRITE_STATES)}",
            )

        repository_id = core.get("repository_id")
        if (
            not isinstance(repository_id, int)
            or isinstance(repository_id, bool)
            or repository_id <= 0
        ):
            error(
                "deployment.core_repository_id",
                f"{path}:core.repository_id",
                "must be a positive integer repository ID "
                "(security identity; owner/name is navigation only)",
            )
        commit = core.get("commit")
        if not (
            isinstance(commit, str)
            and re.fullmatch(r"[0-9a-f]{40}", commit)
        ):
            error(
                "deployment.core_commit",
                f"{path}:core.commit",
                "must be the exact 40-hex deployed commit; abbreviated "
                "SHA and branch/tag/ref names are not valid pins",
            )
        full_name = core.get("repository_full_name")
        if full_name is not None and not (
            isinstance(full_name, str) and full_name.strip()
        ):
            error(
                "deployment.core_navigation",
                f"{path}:core.repository_full_name",
                "optional navigation metadata must be a non-empty string",
            )

    if trust_boundary and isinstance(contract, dict):
        def walk(value, current_path):
            if isinstance(value, dict):
                for key, child in value.items():
                    key_path = (
                        f"{current_path}.{key}"
                        if current_path else str(key)
                    )
                    if key in DEPLOYMENT_TRUST_BOUNDARY_KEYS:
                        error(
                            "deployment.trust_boundary",
                            f"{path}:{key_path}",
                            DEPLOYMENT_TRUST_BOUNDARY_KEYS[key],
                        )
                    walk(child, key_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{current_path}[{index}]")
            elif isinstance(value, str) and CORE_TOKEN_RE.search(value):
                error(
                    "deployment.credential_value",
                    f"{path}:{current_path}",
                    "structurally detected credential/token value",
                )

        walk(contract, "")
        if isinstance(raw_text, str) and CORE_TOKEN_RE.search(raw_text):
            error(
                "deployment.credential_value",
                path,
                "structurally detected credential/token value in contract text",
            )
    return findings


class DeploymentValidator:
    """Deterministic validate_deployment(control_snapshot, deployed_core,
    instance_snapshot, trusted_locator) surface for the V0.4 split planes.

    复用 validate_core() 与 validate_instance()（经 InstanceValidator），
    不复制其逻辑；本面只新增 deployment contract 结构/身份/信任边界检查。
    """
    def __init__(self,control,core,instance,locator):
        self.control=control; self.core=core; self.instance=instance; self.locator_source=locator
        self.findings=[]
    def error(self,c,p,m): self.findings.append(Finding("error",c,p,m))
    def run(self):
        self.resolve_snapshots(); self.load_locator(); self.load_contract()
        if self.contract is None: return self.findings
        self.check_contract_structure(); self.check_trust_boundary()
        self.check_identity(); self.reuse_sub_surfaces()
        return self.findings
    # --- snapshots：必须是携带 trusted provenance 的 RepositorySnapshot ---
    def resolve_snapshots(self):
        self.control_snap=self.core_snap=self.instance_snap=None
        for label,v in (("control",self.control),("core",self.core),("instance",self.instance)):
            if v is None: self.error("deployment.snapshot_missing",f"<{label}-snapshot>",f"{label} snapshot with caller-supplied trusted provenance is required (fail closed)"); continue
            if not isinstance(v,RepositorySnapshot):
                self.error("deployment.snapshot_provenance",f"<{label}-snapshot>",f"{label} snapshot must be a RepositorySnapshot with explicit trusted provenance (root/repository_id[/commit_sha]); bare paths cannot prove identity"); continue
            if not v.root.is_dir(): self.error("deployment.snapshot_root",f"<{label}-snapshot>",f"{label} snapshot root {v.root} is not a directory")
        self.control_snap=self.control if isinstance(self.control,RepositorySnapshot) else None
        self.core_snap=self.core if isinstance(self.core,RepositorySnapshot) else None
        self.instance_snap=self.instance if isinstance(self.instance,RepositorySnapshot) else None
        if self.core_snap is not None and self.core_snap.commit_sha is None:
            self.error("deployment.core_provenance","<core-snapshot>","deployed Core snapshot provenance requires the exact pinned commit_sha (fail closed)")
    # --- trusted locator：唯一的外部信任根 ---
    def load_locator(self):
        self.locator=None
        src=self.locator_source
        if src is None: self.error("deployment.locator","<trusted-locator>","trusted locator is required (fail closed)"); return
        if isinstance(src,dict): d=src
        else:
            p=Path(src)
            if not p.is_file(): self.error("deployment.locator",str(src),"trusted locator file is missing (fail closed)"); return
            try:d=yaml.safe_load(p.read_text(encoding="utf-8"))
            except Exception as e:self.error("deployment.locator",str(src),str(e)); return
        if not isinstance(d,dict): self.error("deployment.locator","<trusted-locator>","trusted locator must be a mapping"); return
        unknown=sorted(set(d)-LOCATOR_TOP_KEYS)
        if unknown: self.error("deployment.locator_keys","<trusted-locator>",f"unknown locator keys {unknown}")
        if "schema_version" in d and d.get("schema_version")!=DEPLOYMENT_SCHEMA:
            self.error("deployment.locator_schema","<trusted-locator>:schema_version",f"must be {DEPLOYMENT_SCHEMA!r}")
        if "document_type" in d and d.get("document_type")!="trusted_locator":
            self.error("deployment.locator_document_type","<trusted-locator>:document_type","must be 'trusted_locator'")
        rc=d.get("runtime_control"); inst=d.get("instance")
        if not isinstance(rc,dict): self.error("deployment.locator_runtime_control","<trusted-locator>:runtime_control","runtime_control block with repository_id is required"); rc={}
        if not isinstance(inst,dict): self.error("deployment.locator_instance","<trusted-locator>:instance","instance block with repository_id is required"); inst={}
        for blk,allowed,where in ((rc,LOCATOR_RC_KEYS,"runtime_control"),(inst,LOCATOR_INSTANCE_KEYS,"instance")):
            u=sorted(set(blk)-allowed)
            if u: self.error("deployment.locator_keys",f"<trusted-locator>:{where}",f"unknown keys {u}")
            v=blk.get("repository_id")
            if not isinstance(v,int) or isinstance(v,bool) or v<=0: self.error("deployment.locator_id",f"<trusted-locator>:{where}.repository_id","must be a positive integer repository ID (security identity)")
            for nk in ("repository","canonical_ref","contract_path"):
                if nk in blk and not (isinstance(blk[nk],str) and blk[nk].strip()):
                    self.error("deployment.locator_field",f"<trusted-locator>:{where}.{nk}","must be a non-empty string (navigation only)")
        if "contract_path" not in rc: rc=dict(rc,contract_path="deployment.yaml")
        if "canonical_ref" not in rc: rc=dict(rc,canonical_ref="main")
        if "canonical_ref" not in inst: inst=dict(inst,canonical_ref="main")
        self.locator={"schema_version":d.get("schema_version",DEPLOYMENT_SCHEMA),"document_type":d.get("document_type","trusted_locator"),"runtime_control":rc,"instance":inst}
    # --- deployment contract：结构化 allowlist + fail closed ---
    def load_contract(self):
        self.contract=None; self.contract_path=None
        if self.locator is None or not isinstance(self.locator.get("runtime_control"),dict): return
        if self.control_snap is None: return
        cp=self.locator["runtime_control"].get("contract_path")
        if cp is None: cp="deployment.yaml"
        p,reason,message=resolve_repository_relative_file(self.control_snap.root,cp)
        if reason is not None:
            where="<trusted-locator>:runtime_control.contract_path"
            if reason=="missing": self.error("deployment.contract_missing",where,f"deployment contract {cp!r} is missing inside the Runtime-Control snapshot (fail closed)")
            else: self.error(f"deployment.contract_path_{reason}",where,f"invalid Runtime-Control contract_path {cp!r}: {message}")
            return
        self.contract_path=p
        try:
            self.contract_text=p.read_text(encoding="utf-8")
            d=yaml.safe_load(self.contract_text)
        except Exception as e:self.error("yaml.parse",p.as_posix(),str(e)); return
        if not isinstance(d,dict): self.error("yaml.mapping",p.as_posix(),"deployment contract must be a mapping"); return
        self.contract=d
    def check_contract_structure(self):
        self.findings.extend(
            validate_deployment_contract_document(
                self.contract,
                path=self.contract_path.as_posix(),
                structure=True,
                trust_boundary=False,
            )
        )
    def check_trust_boundary(self):
        self.findings.extend(
            validate_deployment_contract_document(
                self.contract,
                path=self.contract_path.as_posix(),
                raw_text=self.contract_text,
                structure=False,
                trust_boundary=True,
            )
        )
    def check_identity(self):
        # 身份相等性：locator（TRUST ROOT）与 snapshot provenance（trusted
        # resolver output）双向核对；owner/name 永不参与判断。
        p=self.contract_path.as_posix(); d=self.contract
        if self.control_snap is not None and self.locator is not None:
            exp=self.locator["runtime_control"].get("repository_id")
            if isinstance(exp,int) and self.control_snap.repository_id!=exp:
                self.error("deployment.control_identity","<control-snapshot>",f"resolved Runtime-Control repository ID {self.control_snap.repository_id} != trusted locator expected {exp} (trust failure)")
        core=d.get("core") or {}
        if self.core_snap is not None:
            if isinstance(core.get("repository_id"),int):
                if self.core_snap.repository_id!=core["repository_id"]:
                    self.error("deployment.core_identity","<core-snapshot>",f"contract core.repository_id {core['repository_id']} != trusted snapshot provenance {self.core_snap.repository_id}")
            if isinstance(core.get("commit"),str) and self.core_snap.commit_sha is not None and self.core_snap.commit_sha!=core["commit"]:
                self.error("deployment.core_commit_mismatch","<core-snapshot>",f"contract core.commit {core['commit']} != trusted snapshot provenance {self.core_snap.commit_sha} (deployment must pin the exact deployed commit)")
        if self.instance_snap is not None and self.locator is not None:
            exp=self.locator["instance"].get("repository_id")
            if isinstance(exp,int) and self.instance_snap.repository_id!=exp:
                self.error("deployment.instance_identity","<instance-snapshot>",f"resolved Instance repository ID {self.instance_snap.repository_id} != trusted locator expected {exp} (trust failure)")
    def reuse_sub_surfaces(self):
        # 复用（不复制）：validate_core 管 Core 契约，InstanceValidator 管
        # Instance 契约；binding 由已校验 contract + locator 投影。
        if self.core_snap is not None:
            self.findings.extend(validate_core(self.core_snap.root))
        if self.instance_snap is not None and self.core_snap is not None and self.locator is not None:
            try:
                binding=DeploymentBinding.from_contract(self.contract,self.locator)
                self.findings.extend(InstanceValidator(self.instance_snap.root,self.core_snap.root,binding).run())
            except (KeyError,TypeError,AttributeError):
                self.error("deployment.binding_projection",self.contract_path.as_posix(),"deployment contract is too malformed to project a deployment binding")

def validate_deployment(control_snapshot, deployed_core, instance_snapshot, trusted_locator):
    """V0.4 split deployment validation surface:
    validate_deployment(control_snapshot, deployed_core, instance_snapshot,
    trusted_locator).

    control_snapshot / deployed_core / instance_snapshot: RepositorySnapshot
        objects — materialized trees plus CALLER-SUPPLIED trusted provenance
        (repository_id, and for the deployed Core the exact pinned 40-hex
        commit_sha). Self-declared identity inside repository content is
        never trusted; missing provenance fails closed.
    trusted_locator: the external trust root (dict or YAML file path) with
        runtime_control.repository_id (+ optional canonical_ref/contract_path
        and navigation names) and instance.repository_id. Navigation names
        never participate in trust decisions.

    Reuses validate_core() and validate_instance() instead of duplicating
    them. Explicit non-goals: no resolve_repository(), no lookup_github(),
    no fetch_live_deployment(), no check_platform_permissions() — those are
    future resolver/runtime surfaces. Deterministic and offline; structural
    privacy/ownership boundary enforcement, not complete secret detection.
    """
    return DeploymentValidator(control_snapshot,deployed_core,instance_snapshot,trusted_locator).run()

TRUSTED_PROVENANCE_SECTIONS=("control","core","instance")
TRUSTED_PROVENANCE_KEYS={"repository_id","commit_sha"}

def load_trusted_provenance(path):
    """Load and strictly validate the CLI's trusted snapshot provenance map."""
    p=Path(path)
    try:
        text=p.read_text(encoding="utf-8")
    except (OSError,UnicodeError) as e:
        raise ValueError(f"cannot read file {p}: {e.__class__.__name__}") from None
    try:
        d=yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e.__class__.__name__}") from None
    if not isinstance(d,dict):
        raise ValueError("top-level must be a mapping")
    unknown=[k for k in d if k not in TRUSTED_PROVENANCE_SECTIONS]
    if unknown:
        raise ValueError(f"unknown top-level keys {sorted(unknown,key=repr)!r}")
    for sec in TRUSTED_PROVENANCE_SECTIONS:
        if sec not in d:
            raise ValueError(f"missing required section {sec!r}")
        blk=d[sec]
        if not isinstance(blk,dict):
            raise ValueError(f"section {sec!r} must be a mapping")
        extra=[k for k in blk if k not in TRUSTED_PROVENANCE_KEYS]
        if extra:
            raise ValueError(f"section {sec!r} has unknown keys {sorted(extra,key=repr)!r}")
        if "repository_id" not in blk:
            raise ValueError(f"section {sec!r} missing required repository_id")
        rid=blk["repository_id"]
        if not isinstance(rid,int) or isinstance(rid,bool) or rid<=0:
            raise ValueError(f"section {sec!r}.repository_id must be a positive integer")
        if sec=="core" and "commit_sha" not in blk:
            raise ValueError("section 'core' missing required commit_sha")
        if "commit_sha" in blk:
            cc=blk["commit_sha"]
            if not (isinstance(cc,str) and re.fullmatch(r"[0-9a-f]{40}",cc)):
                raise ValueError(f"section {sec!r}.commit_sha must be an exact 40-lowercase-hex commit")
    return d

def main():
    a=argparse.ArgumentParser(description="Learning OS deterministic validator")
    a.add_argument("root",nargs="?",default=".")
    a.add_argument("--core",action="store_true",help="validate a materialized V0.4 Core plane snapshot (validate_core surface)")
    a.add_argument("--instance",action="store_true",help="validate a materialized V0.4 Instance plane snapshot (validate_instance surface)")
    a.add_argument("--deployment",action="store_true",help="validate a V0.4 split deployment (validate_deployment surface; synthetic offline snapshots)")
    a.add_argument("--core-snapshot",default=None,help="materialized Core snapshot required by --instance/--deployment")
    a.add_argument("--deployment-binding",default=None,help="YAML file with the explicit trusted deployment binding required by --instance (synthetic fixture form)")
    a.add_argument("--control-snapshot",default=None,help="materialized Runtime-Control snapshot root required by --deployment")
    a.add_argument("--instance-snapshot",default=None,help="materialized Instance snapshot root required by --deployment")
    a.add_argument("--locator",default=None,help="YAML file with the trusted locator (external trust root) required by --deployment")
    a.add_argument("--provenance",default=None,help="YAML file with caller-supplied trusted snapshot provenance (control/core/instance repository_id[/commit_sha]) required by --deployment")
    a.add_argument("--write-policy-fingerprint",action="store_true",help="print the write-policy fingerprint computed by this exact Core implementation")
    args=a.parse_args()
    if args.write_policy_fingerprint:
        print(instance_write_policy_fingerprint())
        return 0
    root=Path(args.root)
    if args.deployment:
        need={"--control-snapshot":args.control_snapshot,"--core-snapshot":args.core_snapshot,"--instance-snapshot":args.instance_snapshot,"--locator":args.locator,"--provenance":args.provenance}
        missing=[k for k,v in need.items() if not v]
        if missing: a.error(f"--deployment requires {' '.join(missing)}")
        try: prov=load_trusted_provenance(args.provenance)
        except ValueError as e: a.error(f"invalid --provenance: {e}")
        snaps={}
        for sec,root_key in (("control",args.control_snapshot),("core",args.core_snapshot),("instance",args.instance_snapshot)):
            blk=prov[sec]
            try:
                snaps[sec]=RepositorySnapshot(root_key,blk["repository_id"],blk.get("commit_sha"))
            except ValueError as e: a.error(f"invalid --provenance {sec}: {e}")
        findings=validate_deployment(snaps.get("control"),snaps.get("core"),snaps.get("instance"),args.locator); label="Deployment (offline snapshots)"
    elif args.instance:
        if not args.core_snapshot or not args.deployment_binding: a.error("--instance requires --core-snapshot and --deployment-binding")
        findings=validate_instance(root,args.core_snapshot,args.deployment_binding); label="Instance snapshot"
    elif args.core:
        findings=validate_core(root); label="Core snapshot"
    else:
        v=Validator(root); findings=v.run(); label=f"{len(v.docs)} canonical YAML documents"
    for x in findings: print(x.render())
    e=[x for x in findings if x.severity=="error"]; print(f"validated {label}: {len(e)} error(s)"); return 1 if e else 0
if __name__=="__main__":raise SystemExit(main())
