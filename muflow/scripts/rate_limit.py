"""Classify HTTP 429 only with verified provider-specific evidence."""
import datetime,email.utils,urllib.error

def rejected_429(exc,contract,default_delay):
    cfg=contract.get('rate_limit',{})
    if not isinstance(exc,urllib.error.HTTPError) or exc.code!=429:return None
    if cfg.get('http_429_rejected_before_task') is not True or not cfg.get('rejection_evidence'):return None
    delay=float(default_delay);v=exc.headers.get('Retry-After') if exc.headers else None
    if v:
        try:delay=max(delay,float(v))
        except ValueError:
            try:
                dt=email.utils.parsedate_to_datetime(v)
                if dt.tzinfo is None:dt=dt.replace(tzinfo=datetime.timezone.utc)
                delay=max(delay,(dt-datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            except (TypeError,ValueError,OverflowError):pass
    no_charge=cfg.get('http_429_no_charge') is True and bool(cfg.get('no_charge_evidence'))
    return {'http_status':429,'retry_after_seconds':max(0,delay),'rejection_evidence':cfg['rejection_evidence'],
            'confirmed_no_charge':no_charge,'no_charge_evidence':cfg.get('no_charge_evidence') if no_charge else None}
