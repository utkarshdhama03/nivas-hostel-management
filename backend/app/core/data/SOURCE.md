# common-passwords.txt: source and licence (Q80)

Blocklist used by `app/core/passwords.py` (NIST SP 800-63B-4 blocklist requirement, Q71).

| | |
|---|---|
| Project | SecLists, by Daniel Miessler and contributors |
| File | `Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt` (the UK NCSC's list of the 100,000 most common breached passwords) |
| Pinned commit | `cf9f713b562c1f08ff0b79f145d9bfc67247fd91` (2026-11-07) |
| Downloaded from | `https://raw.githubusercontent.com/danielmiessler/SecLists/cf9f713b562c1f08ff0b79f145d9bfc67247fd91/Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt` |
| Downloaded | 2026-11-07, once (Q80). Line endings normalised to LF. |
| Lines | 99,840 |
| SHA-256 (as stored) | `c2e5696882c603b76bb67a47ee970897e5a76fc4c3f5547abe3d0ca340c576e0` |
| Licence | MIT (SecLists). Full text in `common-passwords.LICENSE`. |

Note: 07 §9 named the SecLists "top 100,000" list; the file at that older path no longer exists. This NCSC-derived list in the same repository is its current equivalent and was named in Q80 as the alternative.
