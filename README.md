# issawiki static archive

This and <https://github.com/riceissa/issawiki-archive> are together a static
archive of the Issawiki, which was a MediaWiki wiki that used to be hosted at
<https://wiki.issarice.com>.

`riceissa/issawiki-archive` contains the source MediaWiki files, and the commit
history of that repo is the revision history of the MediaWiki wiki.

This repo, `riceissa/issawiki-static`, contains a static HTML site in the `site`
folder.  It also contains some Python scripts that were used to generate
`riceissa/issawiki-archive` (namely, `replay.py`) and the `site` static dump
(namely, `export_html.py`).  Both of these scripts were written by Claude Opus 5.5.

To generate `riceissa/issawiki-archive`, run the following command from this repo:

```bash
mkdir ../issawiki-archive && cd ../issawiki-archive && git init -b main
python3 ../issawiki-static/replay.py ../issawiki-static/issawiki-full-history.xml | git fast-import
git reset --hard main
git log --oneline | wc -l    # should be 2966
```

To generate the `site` folder here in `riceissa/issawiki-static`, run the
following command from this repo (the `rm` command is just so you can run it
multiple times with different flags):

```bash
rm -rf site && python3 export_html.py --cache api-cache.json --repo riceissa/issawiki-archive --site-repo riceissa/issawiki-static --out site --download-mathjax
```

If you want to view the files locally, you can also pass `--file-links`.

Note that this second command was originally also run with `--api
https://wiki.issarice.com/api.php`, but since the MediaWiki wiki is no longer
live, the command above will just use the cached API responses that are stored
in this repo.
