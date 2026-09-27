from django.db import migrations
SQL='''
CREATE OR REPLACE FUNCTION core_protect_version() RETURNS trigger AS $$
BEGIN
 IF OLD.sent_at IS NOT NULL OR OLD.historical_status <> '' THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Sent proposal cannot be deleted'; END IF;
  IF ROW(NEW.proposal_id,NEW.number,NEW.development_total,NEW.monthly_fee,NEW.notes,NEW.sent_at,NEW.historical_status) IS DISTINCT FROM ROW(OLD.proposal_id,OLD.number,OLD.development_total,OLD.monthly_fee,OLD.notes,OLD.sent_at,OLD.historical_status) THEN RAISE EXCEPTION 'Sent proposal content is immutable'; END IF;
  IF (OLD.accepted_at IS NOT NULL OR OLD.historical_status='accepted') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Accepted proposal is immutable'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION core_protect_line() RETURNS trigger AS $$
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') AND EXISTS(SELECT 1 FROM core_proposalversion WHERE id=OLD.version_id AND (sent_at IS NOT NULL OR historical_status<>'')) THEN RAISE EXCEPTION 'Sent lines are immutable'; END IF;
 IF TG_OP IN ('INSERT','UPDATE') AND EXISTS(SELECT 1 FROM core_proposalversion WHERE id=NEW.version_id AND (sent_at IS NOT NULL OR historical_status<>'')) THEN RAISE EXCEPTION 'Sent lines are immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
'''
class Migration(migrations.Migration):
    dependencies=[('core','0007_emailoutbox_proposalversion_historical_status_and_more')]
    operations=[migrations.RunSQL(SQL)]
