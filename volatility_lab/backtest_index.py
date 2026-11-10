"""Run-local indexes: no persistent market-data copy and no change in fill rules."""
from bisect import bisect_left,bisect_right
from datetime import datetime


class ContractIndex:
    def __init__(self,contracts):
        self.chains={}
        for contract in contracts:
            self.chains.setdefault(contract['expiry'],[]).append(contract)
        self.expiries=[(expiry,datetime.fromisoformat(expiry).date()) for expiry in sorted(self.chains)]
        self._strikes={};self._legs={}

    def eligible(self,day,minimum,maximum):
        return [expiry for expiry,expiry_day in self.expiries if minimum<=(expiry_day-day).days<=maximum]

    def strikes(self,expiry):
        if expiry not in self._strikes:
            self._strikes[expiry]=sorted({float(row['strike_price']) for row in self.chains[expiry]})
        return self._strikes[expiry]

    def exact(self,expiry,kind,target):
        key=(expiry,kind)
        if key not in self._legs:
            grouped={}
            for row in self.chains[expiry]:
                if row['instrument_type']==kind:
                    grouped.setdefault(float(row['strike_price']),[]).append(row)
            self._legs[key]=(sorted(grouped),grouped)
        strikes,grouped=self._legs[key]
        # Preserve the engine's strict tolerance and reject duplicate definitions.
        # Inclusive bisect bounds only narrow the search; abs() is authoritative.
        possible=strikes[bisect_left(strikes,target-.001):bisect_right(strikes,target+.001)]
        return [row for strike in possible if abs(strike-target)<.001 for row in grouped[strike]]


class SignalIndex:
    def __init__(self,rows):
        self.sessions={}
        for expiry,available,row in rows:
            self.sessions.setdefault((expiry,available.date().isoformat()),[]).append((expiry,available,row))

    def for_entry(self,expiry,day):
        # Keep input order: decide() deliberately chooses the last eligible row.
        return self.sessions.get((expiry,day),())
