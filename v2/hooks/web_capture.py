"""Offline general-web capture contract. Does not fetch or authenticate URLs."""
import copy
import hashlib
import html
import json
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs, parse_qsl, urlencode
from datetime import datetime
if __package__:
    from .preservation_audit import CaptureTree, Node, MAX_HTML_BYTES, VOID
else:
    from preservation_audit import CaptureTree, Node, MAX_HTML_BYTES, VOID

VERSION = 'web_capture-1'
ALLOWED = set('div p span br hr h1 h2 h3 h4 h5 h6 ul ol li dl dt dd blockquote pre code strong b em i u s small sub sup table thead tbody tfoot tr td th figure figcaption a img section article details summary'.split())
DROP = {'script','style','noscript','template','form','input','textarea','button','nav','header','footer','aside','svg','object','embed'}

class WebCaptureError(ValueError):
    pass

def walk(node):
    yield node
    for c in node.children:
        if isinstance(c,Node):yield from walk(c)

def text(node):
    if node.tag in DROP:return ''
    return ''.join(text(c) if isinstance(c,Node) else c for c in node.children)

def compact(value):return re.sub(r'\s+', '', value or '')

def http(value, base=''):
    if not isinstance(value,str) or any(ord(c)<32 for c in value):return ''
    try:
        u=urlsplit(urljoin(base,value))
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:return ''
        return urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path or '/',u.query,u.fragment))
    except ValueError:return ''

def xhost(url):
    h=urlsplit(url).hostname or ''
    return h in ('x.com','twitter.com') or h.endswith(('.x.com','.twitter.com'))

def source_key(url):
    value=http(url)
    if not value:raise WebCaptureError('web_source_url_invalid')
    u=urlsplit(value);host=u.hostname
    if host in ('blog.naver.com','m.blog.naver.com'):
        q=parse_qs(u.query);blog=q.get('blogId',[''])[0];number=q.get('logNo',[''])[0]
        if any(len(set(q.get(k,[])))>1 for k in ('blogId','logNo')):raise WebCaptureError('web_naver_article_identity_ambiguous')
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/(\d+)/?',u.path)
        if match:
            if (blog and blog!=match[1]) or (number and number!=match[2]):raise WebCaptureError('web_naver_article_identity_ambiguous')
            blog,number=match.groups()
        if re.fullmatch(r'[A-Za-z0-9_-]+',blog) and re.fullmatch(r'\d+',number):return 'naver-blog:'+blog+':'+number
        raise WebCaptureError('web_naver_article_identity_missing')
    # Generic query order/fragments can carry meaning. Discard tracking only.
    pairs=[(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if not re.match(r'(?i)^utm_',k) and k.lower() not in ('fbclid','gclid')]
    if u.path=='/' and not pairs and not u.fragment:raise WebCaptureError('web_article_identity_missing')
    return urlunsplit((u.scheme,u.netloc,u.path,urlencode(pairs),u.fragment))

def envelope(raw):
    if len(raw.encode('utf-8'))>MAX_HTML_BYTES:raise WebCaptureError('web_capture_size_limit')
    tree=CaptureTree(raw)
    if any(n.tag=='meta' and n.attrs.get('name')=='x-capture' for n in tree.nodes):return None
    roots=[n for n in tree.nodes if n.tag=='article' and 'web-article' in n.classes() and 'data-source-url' in n.attrs]
    if not roots:return None
    if len(roots)!=1:raise WebCaptureError('web_capture_ambiguous_articles')
    article=roots[0];source=http(article.attrs.get('data-source-url',''))
    if not source or xhost(source) or article.attrs.get('data-tweet-id'):
        raise WebCaptureError('web_capture_invalid_identity')
    bodies=[n for n in walk(article) if 'article-body' in n.classes()]
    if len(bodies)!=1:raise WebCaptureError('web_capture_body_missing_or_ambiguous')
    return article,bodies[0],source

def image_url(n,base):
    value=n.attrs.get('src') or n.attrs.get('data-original') or n.attrs.get('data-src') or ''
    if re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]*={0,2}',value):return value
    return http(value,base)

def render(n,base):
    if isinstance(n,str):return html.escape(n)
    if n.tag in DROP:return ''
    if n.tag in ('video','audio','iframe'):
        raise WebCaptureError('web_capture_unconverted_media')
    inner=''.join(render(c,base) for c in n.children)
    if n.tag not in ALLOWED:return inner
    attrs={}
    if n.tag=='a' and n.attrs.get('href'):
        href=http(n.attrs['href'],base)
        if not href:raise WebCaptureError('web_capture_unsafe_link')
        attrs.update(href=href,rel='noopener noreferrer')
    if n.tag=='img':
        src=image_url(n,base)
        if not src:raise WebCaptureError('web_capture_image_address_missing')
        attrs.update(src=src,alt=n.attrs.get('alt',''))
    for key in ('title','colspan','rowspan'):
        if key in n.attrs:attrs[key]=n.attrs[key]
    # Only known capture semantics; no source style/event/data attributes.
    classes=n.classes() & {'article-body','tweet-text','replies','reply','comment-text','meta','quoted-tweet','reply-quote'}
    if classes:attrs['class']=' '.join(sorted(classes))
    if n.tag=='article' and 'reply' in classes:
        for key in ('data-comment-id','data-author-handle','data-created-at'):
            if n.attrs.get(key):attrs[key]=n.attrs[key]
    attr=''.join(' '+k+'="'+html.escape(str(v),quote=True)+'"' for k,v in attrs.items())
    return '<'+n.tag+attr+'>'+('' if n.tag in VOID else inner+'</'+n.tag+'>')

def capture_regions(article,body):
    # Preserve capture body and sibling media/replies, exclude capture chrome/meta.
    result=[]
    for child in article.children:
        if not isinstance(child,Node):
            if child.strip():raise WebCaptureError('web_capture_unscoped_text')
            continue
        if child is body or any(x is body for x in walk(child)) or child.tag in ('img','figure','video','audio') or 'replies' in child.classes():result.append(child)
        elif child.tag in ('h1',) or 'meta' in child.classes() or 'capture-status' in child.classes():continue
        elif compact(text(child)) or any(n.tag=='img' for n in walk(child)):
            raise WebCaptureError('web_capture_unscoped_content')
    return result

def features(nodes,base):
    links=[];images=[];pieces=[]
    def visit(n):
        if isinstance(n,str):pieces.append(n);return
        if n.tag in DROP:return
        if n.tag=='a' and n.attrs.get('href'):links.append(http(n.attrs['href'],base))
        if n.tag=='img':images.append(image_url(n,base))
        for c in n.children:visit(c)
    for n in nodes:visit(n)
    return compact(''.join(pieces)),Counter(links),Counter(images)

def parse_web(raw):
    env=envelope(raw)
    if env is None:return None
    article,body,source=env;regions=capture_regions(article,body)
    full_html=''.join(render(n,source) for n in regions)
    captured=article.attrs.get('data-saved-at','')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)',captured):raise WebCaptureError('web_capture_saved_at_missing')
    try:datetime.fromisoformat(captured.replace('Z','+00:00'))
    except ValueError:raise WebCaptureError('web_capture_saved_at_invalid')
    title=article.attrs.get('data-title','').strip()
    if not title:
        title=next((text(n).strip() for n in article.children if isinstance(n,Node) and n.tag=='h1'),'')
    if not title:raise WebCaptureError('web_capture_title_missing')
    if not compact(text(body)) and not any(n.tag=='img' for n in walk(body)):raise WebCaptureError('web_capture_body_empty')
    record={'id':'web-'+hashlib.sha256(source_key(source).encode()).hexdigest(),'title':title,
            'author':article.attrs.get('data-author') or article.attrs.get('data-author-handle',''),
            'source_url':source,'source_domain':urlsplit(source).hostname,
            'date_saved':captured[:10],'capturedAt':captured,
            'published_at':article.attrs.get('data-published',''),
            'fullText':text(body).strip(),'fullHtml':full_html,'topics':[]}
    audit=audit_web(raw,record)
    if audit['blocked']:raise WebCaptureError('web_capture_preservation_loss')
    return record,audit

def audit_web(raw,record):
    env=envelope(raw)
    if env is None:raise WebCaptureError('web_capture_not_web')
    article,body,source=env
    expected=features(capture_regions(article,body),source)
    emitted=CaptureTree(record.get('fullHtml',''))
    actual=features(emitted.root.children,source)
    blocked=expected!=actual or compact(text(body))!=compact(record.get('fullText','')) or record.get('source_url')!=source
    return {'version':VERSION,'scope':'web_capture_html_to_record','source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'conversion_status':'loss_or_uncheckable' if blocked else 'preserved','capture_completeness':'unknown',
            'blocked':blocked,'issues':[{'code':'web_capture_preservation_loss','blocking':True}] if blocked else [],
            'counts':{'images':sum(expected[2].values()),'links':sum(expected[1].values())}}

def normalize_web(a):
    # Keep legacy output stable; new namespaced records never get an X ID slot.
    result={'id':a.get('id'),'type':'web','title':a.get('title',''),'author':a.get('author',''),
            'source_url':a.get('source_url',''),'sourceUrl':a.get('source_url',''),
            'source_domain':a.get('source_domain',''),'date_saved':a.get('date_saved',''),
            'topics':a.get('topics',[]) if isinstance(a.get('topics'),list) else [],
            'summary':a.get('summary',''),'fullText':a.get('fullText',''),'fullHtml':a.get('fullHtml',''),'category':'web'}
    if not str(a.get('id','')).startswith('web-'):result['tweetId']=a.get('id')
    for key in ('capturedAt','published_at'):
        if key in a:result[key]=a[key]
    return result

def merge_web(index,incoming,file_id,fingerprint):
    if not isinstance(index,dict) or not isinstance(index.get('articles'),list):raise WebCaptureError('web_index_invalid')
    result=copy.deepcopy(index);items=result['articles'];seen=set();match=None
    for a in items:
        if not isinstance(a,dict) or not isinstance(a.get('id'),str) or not a['id'] or a['id'] in seen:raise WebCaptureError('web_index_identity_invalid')
        seen.add(a['id'])
        try:same=source_key(a.get('source_url',''))==source_key(incoming['source_url'])
        except WebCaptureError:same=False
        if same:
            if match is not None:raise WebCaptureError('web_index_duplicate_source')
            match=a
    if match is None:
        if incoming['id'] in seen:raise WebCaptureError('web_index_id_collision')
        items.append(copy.deepcopy(incoming));return result,incoming['id'],1,0,[]
    proposals=[];changed=False
    content_conflict=any(match.get(k) not in (None,'') and match[k]!=incoming.get(k) for k in ('fullText','fullHtml'))
    for key in ('title','author','source_domain','fullText','fullHtml','published_at'):
        value=incoming.get(key)
        if value in (None,'',[],{}):continue
        if match.get(key) in (None,'',[],{}) and not (content_conflict and key in ('fullText','fullHtml')):match[key]=copy.deepcopy(value);changed=True
        elif match[key]!=value:
            proposals.append({'kind':'web_capture_conflict','recordId':match['id'],'field':key,'fileId':file_id,'fingerprint':fingerprint,
                              'existing':match[key],'incoming':value})
    return result,match['id'],0,int(changed),proposals
