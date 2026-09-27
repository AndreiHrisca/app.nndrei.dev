from django.db import migrations

SQL = '''
CREATE FUNCTION core_reject_audit_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Audit records are insert-only'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_activity BEFORE UPDATE OR DELETE ON core_activityevent FOR EACH ROW EXECUTE FUNCTION core_reject_audit_mutation();
CREATE TRIGGER immutable_stages BEFORE UPDATE OR DELETE ON core_stagechange FOR EACH ROW EXECUTE FUNCTION core_reject_audit_mutation();
CREATE FUNCTION core_protect_version() RETURNS trigger AS $$
BEGIN
 IF OLD.sent_at IS NOT NULL THEN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Sent proposal cannot be deleted'; END IF;
  IF ROW(NEW.proposal_id,NEW.number,NEW.development_total,NEW.monthly_fee,NEW.notes,NEW.sent_at) IS DISTINCT FROM ROW(OLD.proposal_id,OLD.number,OLD.development_total,OLD.monthly_fee,OLD.notes,OLD.sent_at) THEN
   RAISE EXCEPTION 'Sent proposal content is immutable';
  END IF;
  IF OLD.accepted_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Accepted proposal is immutable'; END IF;
 END IF;
 IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON core_proposalversion FOR EACH ROW EXECUTE FUNCTION core_protect_version();
CREATE FUNCTION core_protect_line() RETURNS trigger AS $$
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') AND EXISTS(SELECT 1 FROM core_proposalversion WHERE id=OLD.version_id AND sent_at IS NOT NULL) THEN RAISE EXCEPTION 'Sent lines are immutable'; END IF;
 IF TG_OP IN ('INSERT','UPDATE') AND EXISTS(SELECT 1 FROM core_proposalversion WHERE id=NEW.version_id AND sent_at IS NOT NULL) THEN RAISE EXCEPTION 'Sent lines are immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_lines BEFORE INSERT OR UPDATE OR DELETE ON core_proposalline FOR EACH ROW EXECUTE FUNCTION core_protect_line();
'''
REVERSE = '''
DROP TRIGGER immutable_lines ON core_proposalline;
DROP FUNCTION core_protect_line();
DROP TRIGGER immutable_version ON core_proposalversion;
DROP FUNCTION core_protect_version();
DROP TRIGGER immutable_activity ON core_activityevent;
DROP TRIGGER immutable_stages ON core_stagechange;
DROP FUNCTION core_reject_audit_mutation();
'''
class Migration(migrations.Migration):
    dependencies=[('core','0003_proposalsequence_proposal_request_proposal_and_more')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
