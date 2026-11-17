"""Private networth quotes over the live market, with missing-price inputs supplied by the bot.

The overlay owns its caches and filtered listing buckets; no personal fallback or owner
filter can change the shared market engine used by the site and other players.
"""
import copy
import math
import re
import time

from .engine import Engine
from .items import Item, from_json

VERSION = 'cofl-nw-v1'
FORCE_LABELS = ('explicit NPC policy', 'instant buy', 'your estimate', 'bazaar')


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class Overlay(Engine):
    def _unit(self, entry, seen):
        if entry in self.npc_parts:
            return self.npc_parts[entry] or 0, True
        value, estimated = super()._unit(entry, seen)
        if not value and positive(self.fallback_parts.get(entry)):
            return self.fallback_parts[entry], True
        return value, estimated

    def estimate(self, item, exclude=None):
        result = super().estimate(item, exclude)
        if self.forced and item.entry == self.target_entry:
            result.update(value=self.forced_base or 0, nw_source=self.forced_label)
            if not self.forced_base:
                return result
            built, bare, shares = self.built_value(item, exclude)
            if built:
                result.update(value=built, built=built, bare=bare, shares=shares)
            return result
        if result['value']:
            return result
        # New scan-only recipes have no AH lookup, but their ingredients can price them.
        value = self.craft_cost(item.entry)
        source = 'craft fallback'
        if not value:
            value = self.fallback_base
            source = 'existing base fallback'
        if not positive(value):
            return result
        result.update(value=value, nw_source=source)
        built, bare, shares = self.built_value(item, exclude)
        if built:
            result.update(value=built, built=built, bare=bare, shares=shares)
        return result


_trusted = {}


def trusted_reports(reported):
    """Receipt prices without bundle shares. A bundle gives one number for everything in it, so
    each item's share is a guess: one 133b receipt made all twelve Shimmer and Baby skins 12.9b
    each. Fear, 2026-11-16: "we cant tell what they valued each skin ... dont use those".
    Stated prices and items that went alone stay. Filtered once per solve, not per item."""
    hit = _trusted.get('rows')
    if hit is None or hit[0] is not reported:
        hit = (reported, {e: r for e, r in reported.items() if r.get('how') != 'share'})
        _trusted['rows'] = hit
    return hit[1]


def overlay(engine, entry, owner, fallback_base, fallback_parts, npc_parts):
    fresh = copy.copy(engine)
    fresh.__class__ = Overlay
    fresh.reported = trusted_reports(engine.reported)
    fresh._part_cache = {}
    fresh._craft_cache = {}
    fresh.fallback_base = fallback_base or 0
    fresh.fallback_parts = fallback_parts
    fresh.npc_parts = npc_parts
    # Only the target lookup can supply a listing to this item's or its bare quote.
    found = fresh.lookups.get(entry)
    if owner and found:
        fresh.lookups = dict(fresh.lookups)
        view = copy.copy(found)
        view.buckets = {}
        for key, bucket in found.buckets.items():
            part = copy.copy(bucket)
            part.lbins = [r for r in bucket.lbins if (r.seller or '').casefold() != owner]
            view.buckets[key] = part
        fresh.lookups[entry] = view
    return fresh


def cosmetic_parts(item):
    return [(kind, entry) for kind, entry in (('skin', item.skin), ('skin', item.pet_skin),
                                              ('dye', item.dye), ('rune', getattr(item, 'rune', None))) if entry]


def one(service, row, owner, npc_parts):
    raw = row['item']
    from .account_tracker import validate_linked_item
    try:
        validate_linked_item(raw, service.catalog)
        item = from_json(raw, service.catalog)
    except ValueError:
        # Out of a real item's bounds: One player owns a staff-made Aspect of the End with 500
        # synchronizers and 20 stars. Refusing the whole batch read as an outage on the bot,
        # which dropped every lookup to its old prices for 15 s and aborted each leaderboard
        # reprice (2026-11-16). This one row goes back unpriced; the bot prices it itself.
        return {'value': None, 'non_cosmetic_value': None, 'cosmetic_value': 0, 'extras': [], 'source': 'invalid item'}
    aliases = row.get('aliases') or []
    if not isinstance(aliases, list) or len(aliases) > 20:
        raise ValueError('invalid aliases')
    if item.pet and item.entry not in service.engine.lookups:
        item.entry = next((e for e in aliases if isinstance(e, str) and e in service.engine.lookups), item.entry)
    fallback = row.get('fallback_base')
    if fallback is not None and not positive(fallback):
        raise ValueError('invalid fallback')
    parts = row.get('fallback_parts') or {}
    if not isinstance(parts, dict) or len(parts) > 64 or any(not isinstance(k,str) or len(k)>128 or not positive(v) for k,v in parts.items()):
        raise ValueError('invalid fallback parts')
    engine = overlay(service.engine, item.entry, owner, fallback, parts, npc_parts)
    engine.forced = 'force_base' in row
    engine.forced_base = row.get('force_base')
    # The bot says which of its rules forced the base; only known labels reach its readout.
    label = row.get('force_label') or 'explicit NPC policy'
    if label not in FORCE_LABELS:
        raise ValueError('invalid explicit base policy')
    engine.forced_label = label
    engine.target_entry = item.entry
    if engine.forced_base is not None and not positive(engine.forced_base):
        raise ValueError('invalid explicit base policy')
    full = engine.estimate(item)
    stripped = copy.copy(item)
    stripped.skin = stripped.pet_skin = stripped.dye = None
    if hasattr(stripped, 'rune'):
        stripped.rune = None
    core = engine.estimate(stripped)
    value = full['value'] or 0
    noncos = min(value, core['value'] or value)
    definition=service.catalog.items.get(item.entry) or {}
    if definition.get('cosmetic') or definition.get('category')=='COSMETIC' or item.entry.startswith(('pet_skin_','dye_')) or item.entry.endswith(('_skin','_dye','_rune','_sound')):
        noncos=0
    if not positive(value):
        return {'value':None, 'non_cosmetic_value':None, 'cosmetic_value':0, 'extras':[], 'source':'unpriced'}
    delta = max(value - noncos, 0)
    cosmetics=[]
    for kind, entry in cosmetic_parts(item):
        unit = engine.price_of(entry)[0]
        keep = engine.p.retention.get(kind, engine.p.retention.get('skin', .3))
        cosmetics.append((entry, unit*keep if unit else None))
    total = sum(n for e,n in cosmetics if n)
    extras=[{'entry':entry,'value':delta*n/total if n is not None and total else (0 if n is not None else None),
             'included':False,'cosmetic':True} for entry,n in cosmetics]
    for entry in (item.engine, item.tank, item.module, item.pet_item):
        if entry:
            extras.append({'entry':entry,'value':0 if engine.price_of(entry)[0] else None,'included':True,'cosmetic':False})
    for elem in engine.select_valuable(item):
        if elem.kind in ('skin','dye','rune'):
            continue
        if elem.kind == 'reforge':
            if not elem.coins:
                extras.append({'entry':'reforge_'+item.reforge.replace(' ','_'),'name':item.reforge.title()+' reforge',
                               'value':None,'included':True,'cosmetic':False})
            continue
        if elem.kind in ('recomb','stars','coated'):
            entry={'recomb':'recombobulator_3000','stars':'ritual_residue','coated':'void_powder_coating'}[elem.kind]
            extras.append({'entry':entry,'value':0 if elem.coins else None,'included':True,'cosmetic':False})
    for entry in getattr(item,'scrolls', ()):
        extras.append({'entry':entry,'value':0 if engine.price_of(entry)[0] else None,'included':True,'cosmetic':False})
    return {'value':value,'non_cosmetic_value':noncos,'cosmetic_value':delta,'extras':extras,
            'source':full.get('nw_source') or 'market','confidence':full.get('confidence'),
            'exact':bool(full.get('exact'))}


def batch(service, body):
    rows=body.get('items')
    owner=body.get('owner') or ''
    if not isinstance(owner,str):
        raise ValueError('invalid owner')
    owner=owner.casefold()
    if owner and not re.fullmatch(r'[a-z0-9_]{1,16}',owner):
        raise ValueError('invalid owner')
    if not isinstance(rows,list) or not 0<len(rows)<=64:
        raise ValueError('send 1 to 64 items')
    npc_parts=body.get('npc_parts') or {}
    if not isinstance(npc_parts,dict) or len(npc_parts)>2000 or any(not isinstance(k,str) or len(k)>128 or (v is not None and not positive(v)) for k,v in npc_parts.items()):
        raise ValueError('invalid NPC policy')
    with service.lock:
        stamp=service.last_poll or service.built_at
        if not service.engine or not stamp or time.time()*1000-stamp>180000:
            raise ValueError('market snapshot is stale')
        estimates=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('item'),dict):
                raise ValueError('invalid quote item')
            estimates.append(one(service,row,owner,npc_parts))
        return {'estimates':estimates,'source_ms':stamp,'version':VERSION}
