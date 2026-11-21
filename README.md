# greek-epg

Daily XMLTV guide for Greek TV: Digea (all DTT + regional), ERT, Cosmote TV, Novasports.

A GitHub Action runs every day and force-pushes the result to the `epg` branch
(one commit, no history bloat). A failed scrape leaves the previous guide in place.

## EPG URLs

```
https://raw.githubusercontent.com/<user>/greek-epg/epg/greece.xml
https://raw.githubusercontent.com/<user>/greek-epg/epg/greece.xml.gz
```

## Run manually

Actions → Update EPG → Run workflow.

## Local

```
pip install -r requirements.txt
python3 greek_epg.py -o greece.xml --gz -d 5
```

## Geo-blocking

If Cosmote/Digea return nothing from GitHub's US runners, either:
- add a repo secret `EPG_PROXY` (e.g. `socks5h://user:pass@host:1080` with a Greek exit), or
- register a self-hosted runner on a machine in Greece and set `runs-on: self-hosted`.
