"""Bounded exact circular minimum-edit comparison without full rotation matrices.

For a bound k, k+1 disjoint reference seeds guarantee that a <=k edit global
alignment contains an intact seed. Enumerating its observed occurrence and each
possible preceding net indel displacement [-k,k] therefore covers every <=k
rotation. Candidate/band budgets must be exhausted explicitly, never truncated.
This is a full-sequence comparison, not local alignment to a doubled reference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMPLEMENT = str.maketrans('ACGTN','TGCAN')


class CircularAlignmentUnresolved(ValueError):
    """The exact search could not be completed within its resource budget."""


@dataclass
class Budget:
    max_candidates: int = 4096
    max_cells: int = 50_000_000
    max_trace_bytes: int = 32_000_000
    max_edits: int = 64
    cells: int = 0

    def charge(self, count: int) -> None:
        self.cells += count
        if self.cells > self.max_cells:
            raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_WORK_BUDGET_EXCEEDED')


def _banded(reference: str, observed: str, k: int, budget: Budget, trace: bool=False):
    n,m=len(reference),len(observed)
    if abs(n-m)>k:
        return k+1, None
    width=2*k+1
    if trace and (n+1)*width>budget.max_trace_bytes:
        raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_TRACE_BUDGET_EXCEEDED')
    directions=bytearray((n+1)*width) if trace else None
    inf=k+1
    previous={j:j for j in range(min(m,k)+1)}
    if directions is not None:
        for j in range(1,min(m,k)+1):
            directions[j+k]=2
    for i in range(1,n+1):
        lo,hi=max(0,i-k),min(m,i+k)
        budget.charge(hi-lo+1)
        current={}
        for j in range(lo,hi+1):
            diagonal=previous.get(j-1,inf)+(reference[i-1]!=observed[j-1]) if j else inf
            left=current.get(j-1,inf)+1
            up=previous.get(j,inf)+1
            value=min(diagonal,left,up,inf)
            current[j]=value
            if directions is not None:
                directions[i*width+j-i+k]=1 if value==diagonal else 2 if value==left else 3
        if min(current.values(),default=inf)>k:
            return inf,None
        previous=current
    cost=previous.get(m,inf)
    if not trace or cost>k:
        return cost,None
    atomic=[];i=n;j=m
    while i or j:
        operation=directions[i*width+j-i+k]
        if operation==1 and i and j:
            atomic.append(('equal' if reference[i-1]==observed[j-1] else 'replace',i-1,i,j-1,j));i-=1;j-=1
        elif operation==2 and j:
            atomic.append(('insert',i,i,j-1,j));j-=1
        elif operation==3 and i:
            atomic.append(('delete',i-1,i,j,j));i-=1
        else:
            raise AssertionError('bounded circular traceback is inconsistent')
    opcodes=[]
    for operation in reversed(atomic):
        if opcodes and opcodes[-1][0]==operation[0] and opcodes[-1][2]==operation[1] and opcodes[-1][4]==operation[3]:
            tag,i1,_,j1,_=opcodes[-1];opcodes[-1]=(tag,i1,operation[2],j1,operation[4])
        else:
            opcodes.append(operation)
    return cost,opcodes


def _rotations(reference: str, observed: str, k: int, budget: Budget):
    n,m=len(reference),len(observed)
    if n<=k or not m:
        raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_SEED_BOUND_UNRESOLVED')
    width=n//(k+1)
    result=set()
    for orientation,oriented in (('forward',observed),('reverse_complement',observed.translate(COMPLEMENT)[::-1])):
        doubled=oriented+oriented
        for block in range(k+1):
            start=block*width;seed=reference[start:start+width]
            occurrence=doubled.find(seed)
            while 0<=occurrence<m:
                for displacement in range(-k,k+1):
                    result.add((orientation,(occurrence-start-displacement)%m))
                    if len(result)>budget.max_candidates:
                        raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_CANDIDATE_BUDGET_EXCEEDED')
                occurrence=doubled.find(seed,occurrence+1)
    return sorted(result)


def alignment_key(opcodes, normalized, orientation, offset):
    edits=[op for op in opcodes if op[0]!='equal']
    boundary=1
    if edits and edits[0][1]==0 and edits[-1][2]==sum(op[2]-op[1] for op in opcodes) and edits[0][0] in {'insert','delete'} and edits[-1][0] in {'insert','delete'}:
        boundary=0
    return (len(edits),boundary,tuple(edits),normalized,orientation,offset)


def align_circular(reference: str, observed: str, budget: Budget | None=None) -> dict[str,Any]:
    """Return a proven global optimum, or explicitly fail as unresolved."""
    budget=budget or Budget()
    if not reference or not observed:
        raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_EMPTY_SEQUENCE')
    k=max(1,abs(len(reference)-len(observed)))
    oriented={'forward':observed,'reverse_complement':observed.translate(COMPLEMENT)[::-1]}
    while k<=budget.max_edits:
        candidates=_rotations(reference,observed,k,budget)
        minimum=k+1;finalists=[]
        for orientation,offset in candidates:
            sequence=oriented[orientation];normalized=sequence[offset:]+sequence[:offset]
            cost,_=_banded(reference,normalized,k,budget)
            if cost<minimum:
                minimum=cost;finalists=[]
            if cost==minimum and cost<=k:
                finalists.append((orientation,offset))
        if finalists:
            best=None;best_key=None
            for orientation,offset in finalists:
                sequence=oriented[orientation];normalized=sequence[offset:]+sequence[:offset]
                cost,opcodes=_banded(reference,normalized,minimum,budget,trace=True)
                if cost!=minimum or opcodes is None:
                    raise AssertionError('circular score and traceback disagree')
                key=alignment_key(opcodes,normalized,orientation,offset)
                if best_key is None or key<best_key:
                    matches=sum(i2-i1 for tag,i1,i2,j1,j2 in opcodes if tag=='equal')
                    best={
                        'orientation':orientation,'rotation_offset':offset,
                        'normalized_observed':normalized,'opcodes':opcodes,
                        'matches':matches,'edit_cost':cost,
                        'identity_fraction':matches/max(len(reference),len(observed),1),
                        'canonicalization':'bounded_exact_seed_and_band_v1',
                    }
                    best_key=key
            return best
        if k==budget.max_edits:
            break
        k=min(budget.max_edits,max(k+1,2*k))
    raise CircularAlignmentUnresolved('CIRCULAR_ALIGNMENT_EDIT_BOUND_UNRESOLVED')
