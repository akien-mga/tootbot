import os.path
import sys
import feedparser
from mastodon import Mastodon
import json
import requests
import re
import sqlite3
from datetime import datetime, date, time, timedelta
import argparse
import urllib.parse

parser = argparse.ArgumentParser(description='Synchronize a Twitter account or search using TwitRSS to Mastodon.')
group_twitter_or_search = parser.add_mutually_exclusive_group(required=True)
group_twitter_or_search.add_argument('--search', '-s', nargs=1, help='a search query, like %23GodotEngine+exclude%3Areplies')
group_twitter_or_search.add_argument('--twitter_account', '-t', nargs=1, help='a twitter account')
parser.add_argument('mastodon_login', help='the mastodon login')
parser.add_argument('mastodon_passwd', help='the mastodon password')
parser.add_argument('mastodon_instance', help='the mastodon instance')
parser.add_argument('--days', '-d', type=int, help='number of days')
args = parser.parse_args()


#if len(sys.argv) < 4:
    #print("Usage: python3 tootbot.py twitter_account mastodon_login mastodon_passwd mastodon_instance")
    #print("Or: python3 tootbot.py -s search mastodon_login mastodon_passwd mastodon_instance")
    #sys.exit(1)

# sqlite db to store processed tweets (and corresponding toots ids)
sql = sqlite3.connect('tootbot.db')
db = sql.cursor()
db.execute('''CREATE TABLE IF NOT EXISTS tweets (tweet text, toot text, twitter text, search text, mastodon text, instance text)''')

instance = args.mastodon_instance
days = args.days
if days == None:
    days = 1

search = None
if args.search != None:
    search = urllib.parse.quote_plus(args.search[0])
twitter = None
if args.twitter_account != None:
    twitter = args.twitter_account[0]
mastodon = args.mastodon_login
passwd = args.mastodon_passwd

mastodon_api = None
is_search = False

if search == None:
    d = feedparser.parse('http://twitrss.me/twitter_user_to_rss/?user='+twitter)
    is_search = False
elif twitter == None:
    d = feedparser.parse('http://twitrss.me/twitter_search_to_rss/?term='+search)
    is_search = True

for t in reversed(d.entries):
    # check if this tweet has been processed
    if is_search:
        db.execute('SELECT * FROM tweets WHERE tweet = ? AND search = ? and mastodon = ? and instance = ?',(t.id, search, mastodon, instance))
    else:
        db.execute('SELECT * FROM tweets WHERE tweet = ? AND twitter = ? and mastodon = ? and instance = ?',(t.id, twitter, mastodon, instance))
    last = db.fetchone()

    # process only unprocessed tweets less than 1 day old
    if last is None and (datetime.now()-datetime(t.published_parsed.tm_year, t.published_parsed.tm_mon, t.published_parsed.tm_mday, t.published_parsed.tm_hour, t.published_parsed.tm_min, t.published_parsed.tm_sec) < timedelta(days=days)):
        if mastodon_api is None:
            # Create application if it does not exist
            if not os.path.isfile(instance+'.secret'):
                if Mastodon.create_app(
                    'tootbot',
                    api_base_url='https://'+instance,
                    to_file = instance+'.secret'
                ):
                    print('tootbot app created on instance '+instance)
                else:
                    print('failed to create app on instance '+instance)
                    sys.exit(1)

            try:
                mastodon_api = Mastodon(
                  client_id=instance+'.secret',
                  api_base_url='https://'+instance
                )
                mastodon_api.log_in(
                    username=mastodon,
                    password=passwd,
                    scopes=['read', 'write'],
                    to_file=mastodon+".secret"
                )
            except:
                print("ERROR: First Login Failed!")
                sys.exit(1)

        #h = BeautifulSoup(t.summary_detail.value, "html.parser")
        c = t.title
        if t.author != '(%s)' % twitter:
            c = ("RT %s\n" % t.author[1:-1]) + c
        toot_media = []
        # get the pictures...
        for p in re.finditer(r"https://pbs.twimg.com/[^ \xa0\"]*", t.summary):
            media = requests.get(p.group(0))
            media_posted = mastodon_api.media_post(media.content, mime_type=media.headers.get('content-type'))
            toot_media.append(media_posted['id'])

        # replace t.co link by original URL
        m = re.search(r"http[^ \xa0]*", c)
        if m != None:
            l = m.group(0)
            r = requests.get(l, allow_redirects=False)
            if r.status_code in {301,302}:
                c = c.replace(l,r.headers.get('Location'))

        # remove pic.twitter.com links
        m = re.search(r"pic.twitter.com[^ \xa0]*", c)
        if m != None:
            l = m.group(0)
            c = c.replace(l,' ')

        # remove ellipsis
        c = c.replace('\xa0…',' ')
        
        # add original url
        c = c + "\nOriginal URL: " + t.id

        if toot_media is not None:
            toot = mastodon_api.status_post(c, in_reply_to_id=None, media_ids=toot_media, sensitive=False, visibility='unlisted', spoiler_text=None)
            if "id" in toot:
                db.execute("INSERT INTO tweets VALUES ( ? , ? , ? , ? , ? , ? )",
                (t.id, toot["id"], twitter, search, mastodon, instance))
                sql.commit()
