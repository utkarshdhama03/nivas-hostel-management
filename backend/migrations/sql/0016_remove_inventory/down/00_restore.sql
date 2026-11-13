-- =============================================================================
-- Downgrade of 0016_remove_inventory: the inventory and asset structure exactly as migrations
-- 0001-0015 left it (taken from pg_dump --schema-only of hms at 0015). Deleted rows don't come back.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.apply_stock_movement()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
  UPDATE inventory_items
     SET quantity_on_hand = quantity_on_hand + NEW.quantity, updated_at = app_now()
   WHERE id = NEW.item_id AND org_id = NEW.org_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'inventory item % not found', NEW.item_id USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NULL;
END $function$;

CREATE TABLE public.asset_assignments (
    id uuid DEFAULT uuidv7() NOT NULL,
    org_id uuid NOT NULL,
    hostel_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    room_id uuid,
    user_id uuid,
    student_id uuid,
    assigned_by uuid NOT NULL,
    assigned_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    returned_at timestamp with time zone,
    return_note text,
    CONSTRAINT asset_assignments_check CHECK ((num_nonnulls(room_id, user_id, student_id) = 1)),
    CONSTRAINT asset_assignments_check1 CHECK (((returned_at IS NULL) OR (returned_at >= assigned_at)))
);

ALTER TABLE ONLY public.asset_assignments FORCE ROW LEVEL SECURITY;

ALTER TABLE public.asset_assignments OWNER TO hms_owner;

CREATE TABLE public.assets (
    id uuid DEFAULT uuidv7() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    org_id uuid NOT NULL,
    hostel_id uuid NOT NULL,
    asset_tag text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    status text DEFAULT 'in_store'::text NOT NULL,
    purchased_on date,
    notes text,
    created_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    updated_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    CONSTRAINT assets_asset_tag_check CHECK ((asset_tag ~ '^[A-Z0-9-]{2,30}$'::text)),
    CONSTRAINT assets_category_check CHECK ((category = ANY (ARRAY['furniture'::text, 'electrical'::text, 'electronics'::text, 'kitchen'::text, 'sports'::text, 'other'::text]))),
    CONSTRAINT assets_name_check CHECK (((length(name) >= 2) AND (length(name) <= 80))),
    CONSTRAINT assets_status_check CHECK ((status = ANY (ARRAY['in_store'::text, 'in_use'::text, 'under_repair'::text, 'disposed'::text]))),
    CONSTRAINT assets_version_check CHECK ((version >= 1))
);

ALTER TABLE ONLY public.assets FORCE ROW LEVEL SECURITY;

ALTER TABLE public.assets OWNER TO hms_owner;

CREATE TABLE public.inventory_items (
    id uuid DEFAULT uuidv7() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    org_id uuid NOT NULL,
    hostel_id uuid NOT NULL,
    name text NOT NULL,
    unit text NOT NULL,
    reorder_level numeric(12,3) DEFAULT 0 NOT NULL,
    quantity_on_hand numeric(12,3) DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    updated_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    CONSTRAINT inventory_items_name_check CHECK (((length(name) >= 2) AND (length(name) <= 80))),
    CONSTRAINT inventory_items_quantity_on_hand_check CHECK ((quantity_on_hand >= (0)::numeric)),
    CONSTRAINT inventory_items_reorder_level_check CHECK ((reorder_level >= (0)::numeric)),
    CONSTRAINT inventory_items_unit_check CHECK ((unit = ANY (ARRAY['piece'::text, 'kg'::text, 'litre'::text, 'pack'::text, 'box'::text, 'metre'::text, 'other'::text]))),
    CONSTRAINT inventory_items_version_check CHECK ((version >= 1))
);

ALTER TABLE ONLY public.inventory_items FORCE ROW LEVEL SECURITY;

ALTER TABLE public.inventory_items OWNER TO hms_owner;

CREATE TABLE public.stock_movements (
    id uuid DEFAULT uuidv7() NOT NULL,
    org_id uuid NOT NULL,
    hostel_id uuid NOT NULL,
    item_id uuid NOT NULL,
    movement_type text NOT NULL,
    quantity numeric(12,3) NOT NULL,
    reason text NOT NULL,
    performed_by uuid NOT NULL,
    occurred_at timestamp with time zone DEFAULT public.app_now() NOT NULL,
    CONSTRAINT stock_movements_check CHECK ((((movement_type = 'addition'::text) AND (quantity > (0)::numeric)) OR ((movement_type = 'deduction'::text) AND (quantity < (0)::numeric)) OR (movement_type = 'adjustment'::text))),
    CONSTRAINT stock_movements_movement_type_check CHECK ((movement_type = ANY (ARRAY['addition'::text, 'deduction'::text, 'adjustment'::text]))),
    CONSTRAINT stock_movements_quantity_check CHECK ((quantity <> (0)::numeric)),
    CONSTRAINT stock_movements_reason_check CHECK (((length(reason) >= 3) AND (length(reason) <= 300)))
);

ALTER TABLE ONLY public.stock_movements FORCE ROW LEVEL SECURITY;

ALTER TABLE public.stock_movements OWNER TO hms_owner;

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_org_id_asset_tag_key UNIQUE (org_id, asset_tag);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_org_id_hostel_id_id_key UNIQUE (org_id, hostel_id, id);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_org_id_hostel_id_id_key UNIQUE (org_id, hostel_id, id);

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_org_id_hostel_id_name_key UNIQUE (org_id, hostel_id, name);

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX asset_assignments_one_active_uq ON public.asset_assignments USING btree (asset_id) WHERE (returned_at IS NULL);

CREATE INDEX asset_assignments_org_id_hostel_id_assigned_at_idx ON public.asset_assignments USING btree (org_id, hostel_id, assigned_at DESC);

CREATE INDEX assets_org_id_hostel_id_status_idx ON public.assets USING btree (org_id, hostel_id, status);

CREATE INDEX inventory_low_stock_idx ON public.inventory_items USING btree (org_id, hostel_id) WHERE (is_active AND (quantity_on_hand <= reorder_level));

CREATE INDEX stock_movements_org_id_item_id_occurred_at_idx ON public.stock_movements USING btree (org_id, item_id, occurred_at DESC);

CREATE TRIGGER stock_movements_append_only BEFORE DELETE OR UPDATE ON public.stock_movements FOR EACH ROW EXECUTE FUNCTION public.forbid_change();

CREATE TRIGGER stock_movements_apply AFTER INSERT ON public.stock_movements FOR EACH ROW EXECUTE FUNCTION public.apply_stock_movement();

CREATE TRIGGER stock_movements_no_truncate BEFORE TRUNCATE ON public.stock_movements FOR EACH STATEMENT EXECUTE FUNCTION public.forbid_change();

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_assigned_by_fkey FOREIGN KEY (org_id, assigned_by) REFERENCES public.users(org_id, id);

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_hostel_id_asset_id_fkey FOREIGN KEY (org_id, hostel_id, asset_id) REFERENCES public.assets(org_id, hostel_id, id);

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_hostel_id_room_id_fkey FOREIGN KEY (org_id, hostel_id, room_id) REFERENCES public.rooms(org_id, hostel_id, id);

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_student_id_fkey FOREIGN KEY (org_id, student_id) REFERENCES public.students(org_id, id);

ALTER TABLE ONLY public.asset_assignments
    ADD CONSTRAINT asset_assignments_org_id_user_id_fkey FOREIGN KEY (org_id, user_id) REFERENCES public.users(org_id, id);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_org_id_hostel_id_fkey FOREIGN KEY (org_id, hostel_id) REFERENCES public.hostels(org_id, id);

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_org_id_hostel_id_fkey FOREIGN KEY (org_id, hostel_id) REFERENCES public.hostels(org_id, id);

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_org_id_hostel_id_item_id_fkey FOREIGN KEY (org_id, hostel_id, item_id) REFERENCES public.inventory_items(org_id, hostel_id, id);

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_org_id_performed_by_fkey FOREIGN KEY (org_id, performed_by) REFERENCES public.users(org_id, id);

ALTER TABLE public.asset_assignments ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.assets ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.inventory_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.stock_movements ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON public.asset_assignments USING ((org_id = public.app_current_org())) WITH CHECK ((org_id = public.app_current_org()));

CREATE POLICY tenant_isolation ON public.assets USING ((org_id = public.app_current_org())) WITH CHECK ((org_id = public.app_current_org()));

CREATE POLICY tenant_isolation ON public.inventory_items USING ((org_id = public.app_current_org())) WITH CHECK ((org_id = public.app_current_org()));

CREATE POLICY tenant_isolation ON public.stock_movements USING ((org_id = public.app_current_org())) WITH CHECK ((org_id = public.app_current_org()));

GRANT SELECT,INSERT,UPDATE ON TABLE public.asset_assignments TO hms_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.asset_assignments TO hms_worker;

GRANT SELECT,INSERT,UPDATE ON TABLE public.assets TO hms_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.assets TO hms_worker;

GRANT SELECT,INSERT ON TABLE public.inventory_items TO hms_app;
GRANT SELECT,INSERT ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(version) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(version) ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(name) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(name) ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(unit) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(unit) ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(reorder_level) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(reorder_level) ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(is_active) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(is_active) ON TABLE public.inventory_items TO hms_worker;

GRANT UPDATE(updated_at) ON TABLE public.inventory_items TO hms_app;
GRANT UPDATE(updated_at) ON TABLE public.inventory_items TO hms_worker;

GRANT SELECT,INSERT ON TABLE public.stock_movements TO hms_app;
GRANT SELECT,INSERT ON TABLE public.stock_movements TO hms_worker;
