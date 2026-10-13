import json
import sys
from html.parser import HTMLParser
from pathlib import Path

class Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack=[]; self.cards=[]; self.card=None; self.body=False
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); classes=a.get('class','').split()
        if tag=='details' and 'ib-card' in classes:
            self.card={'type':'ib','id':a.get('data-pin-key'),'title':'','summary':'','fullHtml':'','fullText':'','author':'IB 자료실','topics':[x.get('data-subject') for _,x in self.stack if x.get('data-subject')]+[x.get('data-category') for _,x in self.stack if x.get('data-category')]}
        elif self.card:
            if a.get('data-memo-id'): self.card['memoId']=a['data-memo-id']
            if 'ib-card-link' in classes: self.card['source_url']=a.get('href','')
            if self.body: self.card['fullHtml']+=self.get_starttag_text()
            if 'ib-card-body' in classes: self.body=True
        if tag not in {'img','br','hr','input','meta','link','source','area','wbr','embed'}: self.stack.append((tag,a))
    def handle_endtag(self, tag):
        a=self.stack[-1][1] if self.stack and self.stack[-1][0]==tag else {}
        if self.card:
            if 'ib-card-body' in a.get('class','').split(): self.body=False
            elif self.body: self.card['fullHtml']+='</'+tag+'>'
            if 'ib-card' in a.get('class','').split():
                self.card['topics']=list(dict.fromkeys(self.card['topics'])); self.cards.append(self.card); self.card=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag: del self.stack[i:]; break
    def handle_data(self, data):
        if not self.card:return
        if self.body:self.card['fullHtml']+=data;self.card['fullText']+=data+' '
        classes=[a.get('class','') for _,a in self.stack]
        if any('ib-card-title' in c for c in classes):self.card['title']+=data
        if any('ib-card-desc' in c for c in classes):self.card['summary']+=data
    def handle_entityref(self, name):
        import html
        if self.card and self.body:self.card['fullHtml']+='&'+name+';';self.card['fullText']+=html.unescape('&'+name+';')
    def handle_charref(self, name):
        import html
        if self.card and self.body:self.card['fullHtml']+='&#'+name+';';self.card['fullText']+=html.unescape('&#'+name+';')

root=Path(sys.argv[1] if len(sys.argv)>1 else 'work/archive')
p=Extractor();p.feed((root/'index.html').read_text(encoding='utf-8'))
assert len(p.cards)==63, len(p.cards)
assert all(a['id'] and a['title'] for a in p.cards)
labels={'ib':'IB','math':'수학','science':'과학','english':'영어','chinese':'중국어','admissions':'입시','other':'기타'}
for card in p.cards:card['topics']=list(dict.fromkeys(labels.get(t,t) for t in card['topics']))
(root/'v2/ib-library.json').write_text(json.dumps({'posts':p.cards},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Preserved',len(p.cards),'IB records with original pin and memo IDs')
