"""Feature 12 — Demo dataset.

Six hypothetical, educational case scenarios spanning the main case types, used
to demonstrate the suite. All are fictional; names are placeholders.
"""

from __future__ import annotations

from typing import Dict, List

DEMO_CASES: List[Dict[str, str]] = [
    {
        "id": "fraud",
        "title": "Fake Investment Scheme",
        "case_type": "Fraud",
        "jurisdiction": "Nepal",
        "description": (
            "Mr. K operated an investment company promising a guaranteed 20% monthly return. "
            "Over 18 months he collected about NPR 50 million from 240 small investors via bank "
            "transfers and signed agreements. Early investors were paid 'returns' funded by newer "
            "investors' deposits; no genuine profit-generating activity was found. When deposits "
            "slowed, payouts stopped and communication ceased. Investigators hold bank statements, "
            "signed agreements, marketing brochures promising guaranteed returns, and testimony "
            "from 12 investors. Mr. K claims it was a genuine venture that failed."
        ),
    },
    {
        "id": "crypto",
        "title": "Crypto Token Rug-Pull",
        "case_type": "Financial Scam",
        "jurisdiction": "Nepal",
        "description": (
            "A team launched a cryptocurrency token 'NepCoin' with an anonymous whitepaper and a "
            "Telegram group of 8,000 members. They raised roughly USD 300,000 in a presale, promised "
            "a decentralized exchange listing, then removed all liquidity within 48 hours and deleted "
            "their social channels. On-chain records show funds moving to a centralized exchange "
            "wallet. Two organizers were later identified from a KYC leak. They say the project failed "
            "due to a smart-contract exploit, not intentional wrongdoing."
        ),
    },
    {
        "id": "employment",
        "title": "Wrongful Termination Dispute",
        "case_type": "Employment Dispute",
        "jurisdiction": "Nepal",
        "description": (
            "An employee with four years of service was dismissed one week after raising a written "
            "complaint about unpaid overtime. The employer cites 'restructuring' but hired a "
            "replacement for the same role two weeks later. The employee has the complaint email, "
            "the termination letter with no notice period, pay slips showing unpaid overtime, and two "
            "colleagues willing to testify. The employer argues the role was genuinely redundant and "
            "the timing was coincidental."
        ),
    },
    {
        "id": "property",
        "title": "Disputed Land Boundary and Sale",
        "case_type": "Property Dispute",
        "jurisdiction": "Nepal",
        "description": (
            "Two neighbours dispute ownership of a 200 sq. m strip of land. Party A holds a registered "
            "deed from 2015; Party B claims continuous possession and cultivation since 2001 and "
            "produces witness statements and old tax-payment receipts. A recent government survey map "
            "places the strip within Party A's parcel, but an earlier 1998 map is ambiguous. Party B "
            "alleges the newer survey was conducted without notice to adjoining owners."
        ),
    },
    {
        "id": "cybercrime",
        "title": "Account Takeover and Data Theft",
        "case_type": "Cybercrime",
        "jurisdiction": "Nepal",
        "description": (
            "A company reports that a former IT contractor accessed its customer database two days "
            "after his contract ended, using credentials that were never revoked, and exported around "
            "15,000 customer records. Server logs show the login from an IP address later linked to "
            "the contractor's home connection, though the account was shared among three staff. The "
            "contractor says he was asked informally to 'finish a migration' and had permission. No "
            "written authorization exists."
        ),
    },
    {
        "id": "homicide",
        "title": "Contested Homicide Allegation",
        "case_type": "Murder",
        "jurisdiction": "Nepal",
        "description": (
            "The accused is alleged to have caused the death of a person during a late-night altercation "
            "outside a bar. The prosecution relies on one eyewitness who admits limited lighting, CCTV "
            "footage that is partially obscured, and the accused's presence at the scene. The defense "
            "raises self-defense and disputes the cause of death, noting the post-mortem is inconclusive "
            "between the altercation and a subsequent fall. There is no weapon recovered. The allegation "
            "is unproven and the accused is presumed innocent."
        ),
    },
]

DEMO_INDEX = {d["id"]: d for d in DEMO_CASES}
