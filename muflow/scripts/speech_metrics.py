"""Text capacity estimate, deliberately not a phonetic duration measurement."""
import argparse,json,unicodedata

def spoken_count(text):
    return sum(1 for c in str(text) if not c.isspace() and unicodedata.category(c)[0] not in ('P','S','C'))

def estimate(text,rate):
    if not isinstance(rate,(int,float)) or rate<=0:raise ValueError('Positive project-specific rate required')
    count=spoken_count(text)
    return {'written_characters':len(text),'spoken_character_estimate':count,'rate':rate,'speech_seconds_estimate':count/rate,
            'basis':'Unicode punctuation/symbol/whitespace/control excluded. Numerals/abbreviations require an approved spoken expansion; no measured acting duration.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('text');p.add_argument('--rate',type=float,required=True);a=p.parse_args();print(json.dumps(estimate(a.text,a.rate),ensure_ascii=False))
