"""Role vocabulary: the many words people use for the same job.

A "line cook", a "cook II", a "grill cook" and a "cocinero de línea" all work the same schedule role,
`cook`. A "stocker", a "merchandiser" and a "reponedor" are all `stocker`. A "foreman", a "crew lead" and a
"capataz" are all `foreman`. This module turns whatever title someone types into the schedule role it
belongs to, with what the job involves (duties, skills, usual certifications) in English and Spanish.

Used by:
  hiring    - experience under any title for the role counts, and related roles count in part
              (a bartender's months count partly toward server). The reasons say exactly how.
  roster    - "Line Cook" typed as a schedule role is saved as `cook`, so staffing rules find them.
  Set up role / Team screen - a title suggests a role, its duties and the certs to check.

Rules this module keeps:
  - A title never sets an access level. "General manager" or "Owner's assistant" maps to a role for the
    schedule only. Levels 0 to 4 are set by the account holder, as before (hiwr_access).
  - A title never unlocks pay, payroll or any other data. Nothing here reads or writes pay.
  - A title the catalog doesn't know is kept as typed. A close guess is only a suggestion for a person
    to confirm; it is never saved on its own.
  - House titles (words only this business uses, like "pit boss" for the grill cook) are added by a
    Manager or above and are append-only: retiring one adds an event, history stays.

Standard library only.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass, field

TRADES = ("restaurant", "retail", "lab", "factory", "health", "services", "office")
HOUSE_TITLE_LEVEL = 2          # Manager and up add or retire house titles

# Words around a title that say how senior someone is. They never change the role and never set a level.
SENIORITY = {
    "trainee": "trainee", "intern": "trainee", "apprentice": "apprentice", "aprendiz": "apprentice",
    "helper": "helper", "ayudante": "helper",
    "junior": "junior", "jr": "junior", "entry": "junior", "i": "level 1", "1": "level 1",
    "ii": "level 2", "2": "level 2", "iii": "level 3", "3": "level 3", "iv": "level 4", "4": "level 4",
    "journeyman": "journeyman", "journeyperson": "journeyman", "oficial": "journeyman",
    "senior": "senior", "sr": "senior", "lead": "lead", "head": "lead", "jefe": "lead", "chief": "lead",
    "master": "master", "maestro": "master", "principal": "senior",
    "assistant": "assistant", "asst": "assistant", "sub": "assistant", "segundo": "assistant",
}
SCHEDULE_WORDS = {
    "part": "part time", "pt": "part time", "ft": "full time", "full": "full time", "overnight": "overnight",
    "night": "nights", "nights": "nights", "nocturno": "nights", "weekend": "weekends", "seasonal": "seasonal",
    "temporal": "seasonal", "temp": "temporary", "temporary": "temporary", "on-call": "on call", "call": "on call",
    "eventual": "temporary", "fin": "weekends", "semana": "weekends", "medio": "part time", "tiempo": None,
    "completo": "full time", "time": None, "shift": None, "turno": None, "de": None, "del": None, "la": None,
    "el": None, "the": None, "and": None, "y": None, "of": None,
}


@dataclass(frozen=True)
class Role:
    key: str                       # the schedule role, as staffing_rules and staff.role store it
    trades: tuple
    en: str
    es: str
    aliases_en: tuple = ()
    aliases_es: tuple = ()
    duties: tuple = ()             # (english, spanish)
    skills: tuple = ()
    certs: tuple = ()              # cert ids as candidate_certs stores them; usual, not required by law here
    transfers: dict = field(default_factory=dict)   # other role -> share of months that counts toward this one


def _r(key, trades, en, es, aliases_en="", aliases_es="", duties=(), skills="", certs="", transfers=None):
    split = lambda s: tuple(x.strip() for x in s.split(";") if x.strip())  # noqa: E731
    return Role(key, tuple(trades.split()), en, es, split(aliases_en), split(aliases_es), tuple(duties),
                split(skills), split(certs), dict(transfers or {}))


# ---------------------------------------------------------------- the catalog
# Aliases are separated by ";". Spanish lists both forms where the word changes (cocinero; cocinera).
ROLES = [
    # ---------------- restaurant: kitchen
    _r("chef", "restaurant", "Chef", "Chef",
       "executive chef; head chef; chef de cuisine; kitchen manager; km; culinary director; chef owner; exec chef",
       "chef ejecutivo; chef ejecutiva; jefe de cocina; jefa de cocina; gerente de cocina; chef principal",
       [("Runs the kitchen, menu and food cost", "Dirige la cocina, el menú y el costo de comida"),
        ("Orders from sales, waste and inventory", "Hace pedidos según ventas, merma e inventario"),
        ("Signs off on HACCP corrective actions", "Firma las acciones correctivas HACCP")],
       "menu costing; ordering; scheduling; food safety; training", "servsafe_manager",
       {"sous chef": 0.75, "cook": 0.25}),
    _r("sous chef", "restaurant", "Sous chef", "Sous chef",
       "sous; souschef; junior sous; executive sous chef; chef de partie lead; kitchen supervisor",
       "segundo de cocina; segunda de cocina; subjefe de cocina; subchef; supervisor de cocina; supervisora de cocina",
       [("Runs the line during service", "Dirige la línea durante el servicio"),
        ("Checks prep, pars and temps", "Revisa preparación, pars y temperaturas")],
       "line leadership; prep lists; temps and logs; plating", "servsafe_manager; food_handler",
       {"cook": 0.5, "chef": 1.0}),
    _r("cook", "restaurant", "Cook", "Cocinero",
       "line cook; grill cook; fry cook; fryer; saute cook; sauté cook; broiler cook; broil cook; short order cook; "
       "short-order cook; flat top cook; griddle cook; wok cook; pizza cook; pizzaiolo; pizza maker; sushi cook; "
       "sushi chef; tandoor cook; garde manger; pantry cook; cold line; hot line; line tech; chef de partie; "
       "commis; commis chef; station cook; grill; fry; saute; sauté; broiler; flat top; griddle; wok; pantry; hot side; cold side; banquet cook; breakfast cook; cook i; cook ii; cook iii; cook 1; "
       "cook 2; cook 3; kitchen team member; back of house; boh; culinary associate; cook",
       "cocinero; cocinera; cocinero de linea; cocinera de linea; parrillero; parrillera; freidor; freidora; "
       "plancha; planchero; planchera; cocinero de plancha; pizzero; pizzera; cocinero de turno; cocinero de comal; "
       "taquero; taquera; cocina caliente; cocina fria",
       [("Cooks orders on the line to the recipe", "Cocina las órdenes en la línea según la receta"),
        ("Keeps temp logs at the station", "Lleva el registro de temperaturas de la estación"),
        ("Calls out 86'd items and low stock", "Avisa cuando algo se acaba (86) o queda poco")],
       "knife skills; ticket times; recipe specs; allergen checks; station setup", "food_handler",
       {"sous chef": 1.0, "chef": 1.0, "prep": 0.75, "baker": 0.5, "dishwasher": 0.25}),
    _r("prep", "restaurant", "Prep cook", "Preparador",
       "prep cook; prep; prepper; kitchen prep; prep team; prep assistant; kitchen helper; kitchen assistant; "
       "food prep; food preparer; salad maker; salad prep; morning prep; prep worker",
       "preparador; preparadora; ayudante de cocina; auxiliar de cocina; cocinero de preparacion; "
       "pinche; pinche de cocina; mise en place",
       [("Preps to the par sheet before service", "Prepara según la hoja de par antes del servicio"),
        ("Labels and dates everything (FIFO)", "Etiqueta y pone fecha a todo (PEPS)")],
       "knife skills; par sheets; labeling; FIFO", "food_handler",
       {"cook": 1.0, "dishwasher": 0.25}),
    _r("baker", "restaurant factory", "Baker", "Panadero",
       "baker; pastry cook; pastry chef; pastry; bread baker; cake decorator; donut fryer; bakery associate; "
       "patissier; dessert cook",
       "panadero; panadera; repostero; repostera; pastelero; pastelera; decorador de pasteles; decoradora de pasteles",
       [("Bakes to the production list", "Hornea según la lista de producción"),
        ("Scales recipes and tracks waste", "Escala recetas y registra la merma")],
       "scaling recipes; ovens; proofing; decorating", "food_handler", {"cook": 0.5, "prep": 0.5}),
    _r("dishwasher", "restaurant", "Dishwasher", "Lavaplatos",
       "dishwasher; dish; dishie; dish pit; dish washer; dish machine operator; pot washer; pot and pan; "
       "steward; kitchen steward; porter; kitchen porter; utility; utility worker; kitchen utility; "
       "sanitation; dish room",
       "lavaplatos; lavaloza; lavalozas; friegaplatos; lavatrastes; stewart; steward de cocina; "
       "auxiliar de limpieza de cocina",
       [("Runs the dish machine and checks its temps", "Opera la máquina de platos y revisa sus temperaturas"),
        ("Keeps the kitchen clean and stocked with clean wares", "Mantiene la cocina limpia y con loza limpia")],
       "dish machine temps; chemical safety; speed", "", {"prep": 0.25}),
    _r("expo", "restaurant", "Expo", "Expedidor",
       "expo; expeditor; expediter; food expeditor; pass; window",
       "expedidor; expedidora; despachador de comida; despachadora de comida",
       [("Calls tickets and checks plates before they go out", "Canta las comandas y revisa los platos antes de salir"),
        ("Catches allergy and modifier mistakes (like hidden tomato)",
         "Detecta errores de alergias y modificaciones (como tomate escondido)")],
       "ticket flow; allergen checks; plating", "food_handler", {"cook": 0.5, "food runner": 0.5, "server": 0.25}),
    # ---------------- restaurant: front of house
    _r("server", "restaurant", "Server", "Mesero",
       "server; waiter; waitress; waitperson; waitstaff; wait staff; front server; dining room server; "
       "banquet server; cocktail server; cocktail waitress; captain; table server; front of house; foh; "
       "hospitality associate",
       "mesero; mesera; camarero; camarera; mozo; moza; garzon; garzona; mesero de banquetes",
       [("Takes orders and enters modifiers and allergies", "Toma órdenes y anota modificaciones y alergias"),
        ("Runs the check and closes out", "Cobra la cuenta y hace el cierre")],
       "POS; menu knowledge; allergens; upselling; cash handling", "alcohol_server; food_handler",
       {"bartender": 0.75, "busser": 0.5, "host": 0.5, "food runner": 0.5, "barista": 0.25, "cashier": 0.25}),
    _r("busser", "restaurant", "Busser", "Ayudante de mesero",
       "busser; bus boy; busboy; bus person; busperson; server assistant; back waiter; table busser",
       "ayudante de mesero; ayudante de mesera; garrotero; garrotera; cumis; ayudante de camarero; recogemesas",
       [("Clears and resets tables", "Limpia y monta las mesas"), ("Keeps water and bread going", "Sirve agua y pan")],
       "table turns; teamwork", "", {"food runner": 0.75, "server": 0.5, "host": 0.25}),
    _r("food runner", "restaurant", "Food runner", "Corredor",
       "food runner; runner; food server runner; tray runner",
       "corredor; corredora; runner; llevaplatos",
       [("Runs plates from the window to the table", "Lleva los platos de la ventana a la mesa")],
       "table numbers; speed", "", {"busser": 0.75, "server": 0.5, "expo": 0.5}),
    _r("host", "restaurant", "Host", "Anfitrión",
       "host; hostess; greeter; maitre d; maître d; maitre d'; reservationist; front desk host; seater",
       "anfitrion; anfitriona; hostess; recepcionista de restaurante; jefe de sala",
       [("Seats guests and manages the wait list", "Sienta a los clientes y maneja la lista de espera"),
        ("Takes reservations and late cancels", "Toma reservaciones y cancelaciones tardías")],
       "wait list; reservations; phones", "", {"server": 0.5, "busser": 0.25, "cashier": 0.25}),
    _r("bartender", "restaurant", "Bartender", "Bartender",
       "bartender; bar tender; barkeep; barkeeper; mixologist; bar manager; head bartender; service bartender",
       "cantinero; cantinera; barman; bartender; coctelero; coctelera",
       [("Makes drinks to spec and checks IDs", "Prepara bebidas según receta y revisa identificaciones"),
        ("Counts the bar and logs pours and spills", "Cuenta el bar y registra lo servido y derramado")],
       "drink specs; ID checks; bar inventory; cash handling", "alcohol_server",
       {"barback": 0.5, "server": 0.5, "barista": 0.25}),
    _r("barback", "restaurant", "Barback", "Ayudante de bar",
       "barback; bar back; bar porter; bar assistant",
       "ayudante de bar; ayudante de cantinero; barback",
       [("Restocks the bar, ice and glassware", "Surte el bar, el hielo y la cristalería")],
       "restocking; speed", "", {"busser": 0.5, "bartender": 1.0}),
    _r("barista", "restaurant retail", "Barista", "Barista",
       "barista; coffee maker; espresso bar; coffee bar attendant; cafe associate",
       "barista; cafetero; cafetera",
       [("Makes coffee drinks to spec", "Prepara bebidas de café según receta")],
       "espresso; milk; cash handling", "food_handler", {"cashier": 0.5, "bartender": 0.25, "server": 0.25}),
    _r("delivery driver", "restaurant retail services", "Delivery driver", "Repartidor",
       "delivery driver; driver; courier; delivery; delivery person; route driver; delivery associate",
       "repartidor; repartidora; chofer; chofer repartidor; mensajero; mensajera; conductor; conductora",
       [("Delivers orders and gets proof of delivery", "Entrega órdenes y toma prueba de entrega"),
        ("Logs the route and vehicle", "Registra la ruta y el vehículo")],
       "route planning; customer contact", "drivers_license", {}),
    # ---------------- management (schedule role only; never an access level)
    _r("general manager", "restaurant retail services", "General manager", "Gerente general",
       "general manager; gm; manager; store manager; restaurant manager; operations manager; ops manager; "
       "front of house manager; foh manager; branch manager; location manager; manager on duty; mod",
       "gerente; gerente general; gerente de tienda; gerente de restaurante; administrador; administradora; "
       "encargado general; encargada general",
       [("Runs the day and signs end-of-day", "Dirige el día y firma el cierre"),
        ("Handles disputes and live calls", "Atiende disputas y llamadas en vivo")],
       "scheduling; P&L; staff coaching; customer recovery", "servsafe_manager",
       {"assistant manager": 0.75, "shift lead": 0.5}),
    _r("assistant manager", "restaurant retail services", "Assistant manager", "Subgerente",
       "assistant manager; asst manager; am; assistant store manager; asm; assistant general manager; agm; "
       "floor manager; dining room manager",
       "subgerente; asistente de gerente; gerente asistente; gerente de piso",
       [("Covers for the manager and runs shifts", "Cubre al gerente y dirige turnos")],
       "scheduling; opening and closing; cash counts", "", {"shift lead": 0.75, "general manager": 1.0}),
    _r("shift lead", "restaurant retail factory services", "Shift lead", "Encargado de turno",
       "shift lead; shift leader; shift supervisor; shift manager; supervisor; key holder; keyholder; team lead; "
       "team leader; crew leader; captain of shift; lead associate",
       "encargado de turno; encargada de turno; supervisor de turno; supervisora de turno; lider de turno; "
       "jefe de turno; jefa de turno; encargado; encargada; lider de equipo",
       [("Opens or closes and runs roll call", "Abre o cierra y pasa lista"),
        ("First stop for problems on the shift", "Primer contacto para problemas del turno")],
       "roll call; cash counts; opening and closing", "", {"assistant manager": 1.0}),
    # ---------------- retail
    _r("sales associate", "retail", "Sales associate", "Asociado de ventas",
       "sales associate; associate; retail associate; sales clerk; store clerk; clerk; floor associate; "
       "sales floor associate; salesperson; sales person; sales rep; retail sales; team member; crew member; "
       "store associate; customer associate; department associate",
       "asociado de ventas; asociada de ventas; vendedor; vendedora; dependiente; dependienta; empleado de tienda; "
       "empleada de tienda; asesor de ventas; asesora de ventas; colaborador; colaboradora",
       [("Helps customers on the floor", "Atiende a los clientes en el piso"),
        ("Keeps the floor faced and priced", "Mantiene el piso ordenado y con precios")],
       "customer service; POS; product knowledge", "", {"cashier": 0.75, "stocker": 0.5}),
    _r("cashier", "retail restaurant", "Cashier", "Cajero",
       "cashier; checker; register; front end; front end associate; checkout; till; counter; counter person; "
       "front counter; cashier clerk",
       "cajero; cajera; caja; atencion en caja; mostrador",
       [("Rings sales and handles cash and cards", "Cobra ventas y maneja efectivo y tarjetas"),
        ("Counts the drawer at close", "Cuenta la caja al cierre")],
       "POS; cash handling; returns", "", {"sales associate": 0.75, "barista": 0.5}),
    _r("stocker", "retail", "Stocker", "Reponedor",
       "stocker; stock clerk; stock associate; shelf stocker; overnight stocker; night stocker; merchandiser; "
       "replenishment; replenishment associate; stock person; grocery stocker; stock crew; restocker; "
       "freight; freight crew; unloader; backroom associate",
       "reponedor; reponedora; surtidor; surtidora; acomodador; acomodadora; reabastecedor; reabastecedora; "
       "auxiliar de almacen; mercaderista",
       [("Moves stock from back to floor (on floor vs to stock)", "Pasa mercancía del almacén al piso"),
        ("Faces shelves and checks dates", "Ordena anaqueles y revisa fechas")],
       "FIFO; planograms; pallet jack", "", {"receiver": 0.75, "warehouse associate": 0.75, "sales associate": 0.25}),
    _r("receiver", "retail factory", "Receiver", "Recibidor",
       "receiver; receiving clerk; receiving associate; dock clerk; dock worker; inbound; shipping and receiving",
       "recibidor; recibidora; encargado de recepcion; encargada de recepcion; auxiliar de recibo; recibo de mercancia",
       [("Checks deliveries against the PO (short, damaged, wrong items)",
         "Revisa entregas contra la orden de compra (faltantes, dañados, equivocados)"),
        ("Opens SHIPMENT tickets for problems", "Abre tickets de envío para problemas")],
       "PO matching; UPC scanning; quality checks", "", {"stocker": 0.75, "warehouse associate": 0.75}),
    _r("inventory specialist", "retail factory", "Inventory specialist", "Especialista de inventario",
       "inventory specialist; inventory clerk; inventory control; inventory associate; cycle counter; "
       "inventory analyst; stock controller",
       "especialista de inventario; auxiliar de inventario; controlador de inventario; controladora de inventario; "
       "inventarista",
       [("Runs cycle counts and finds mismatches", "Hace conteos cíclicos y encuentra diferencias")],
       "cycle counts; shrink; spreadsheets", "", {"receiver": 0.5, "stocker": 0.5}),
    _r("visual merchandiser", "retail", "Visual merchandiser", "Visual merchandiser",
       "visual merchandiser; visual; display associate; window dresser; planogram associate",
       "escaparatista; visual merchandiser; decorador de tienda; decoradora de tienda",
       [("Sets displays and planograms", "Monta exhibiciones y planogramas")], "planograms; signage", "",
       {"stocker": 0.5, "sales associate": 0.25}),
    _r("loss prevention", "retail", "Loss prevention", "Prevención de pérdidas",
       "loss prevention; lp; loss prevention associate; asset protection; ap associate; store detective; security",
       "prevencion de perdidas; guardia de seguridad; vigilante; seguridad",
       [("Watches for shrink and logs incidents", "Vigila pérdidas y registra incidentes")],
       "incident reports; cameras", "", {}),
    _r("warehouse associate", "retail factory", "Warehouse associate", "Almacenista",
       "warehouse associate; warehouse worker; warehouse; picker; order picker; packer; picker packer; "
       "fulfillment associate; material handler; shipping clerk; shipper; loader",
       "almacenista; trabajador de almacen; trabajadora de almacen; surtidor de pedidos; empacador; empacadora; "
       "cargador; cargadora; montacarguista auxiliar",
       [("Picks, packs and ships orders", "Surte, empaca y envía pedidos")], "scanning; pallet jack", "",
       {"stocker": 0.75, "receiver": 0.75, "forklift operator": 0.5}),
    _r("forklift operator", "retail factory lab", "Forklift operator", "Montacarguista",
       "forklift operator; forklift driver; lift driver; reach truck operator; hi-lo driver; hilo driver",
       "montacarguista; operador de montacargas; operadora de montacargas",
       [("Moves pallets safely and inspects the lift daily", "Mueve tarimas con seguridad e inspecciona el montacargas")],
       "pallet handling; daily lift inspection", "forklift_cert", {"warehouse associate": 0.5}),
    # ---------------- lab: long jobs, construction
    _r("project manager", "lab office", "Project manager", "Gerente de proyecto",
       "project manager; pm; construction manager; job manager; project lead; project coordinator",
       "gerente de proyecto; gerenta de proyecto; administrador de obra; coordinador de proyecto; coordinadora de proyecto",
       [("Runs phases, inspections and draws", "Maneja fases, inspecciones y pagos por avance"),
        ("Tracks subs and change orders", "Controla subcontratistas y órdenes de cambio")],
       "scheduling; budgets; permits", "", {"superintendent": 0.75, "estimator": 0.5}),
    _r("superintendent", "lab", "Superintendent", "Superintendente",
       "superintendent; super; site superintendent; site manager; general foreman",
       "superintendente; supervisor de obra; supervisora de obra; residente de obra; jefe de obra",
       [("Runs the site day to day and books inspections", "Dirige la obra día a día y agenda inspecciones")],
       "site safety; inspections; crew scheduling", "osha_30", {"foreman": 0.75, "project manager": 0.5}),
    _r("foreman", "lab factory", "Foreman", "Capataz",
       "foreman; forewoman; foreperson; site lead; crew lead; crew chief; lead hand; leadman; working foreman; "
       "field lead; job lead",
       "capataz; mayordomo; mayordoma; cabo; jefe de cuadrilla; jefa de cuadrilla; encargado de obra; "
       "encargada de obra; maestro de obras",
       [("Leads the crew and signs off daily work", "Dirige la cuadrilla y firma el trabajo del día"),
        ("Keeps the punch list", "Lleva la lista de pendientes")],
       "crew leadership; reading plans; safety talks", "osha_30", {"superintendent": 1.0, "carpenter": 0.25,
                                                                   "electrician": 0.25, "plumber": 0.25}),
    _r("carpenter", "lab", "Carpenter", "Carpintero",
       "carpenter; framer; framing carpenter; finish carpenter; trim carpenter; rough carpenter; cabinet installer; "
       "cabinetmaker; joiner",
       "carpintero; carpintera; carpintero de obra; armador; ebanista",
       [("Frames, trims and installs to plan", "Arma estructura, molduras e instala según plano")],
       "framing; reading plans; power tools", "osha_10", {"laborer": 0.25, "drywall installer": 0.25}),
    _r("electrician", "lab", "Electrician", "Electricista",
       "electrician; journeyman electrician; apprentice electrician; master electrician; wireman; sparky; "
       "electrical helper; electrical technician",
       "electricista; tecnico electricista; ayudante de electricista; oficial electricista",
       [("Rough-in and trim-out wiring to code", "Instala cableado a código (preparación y acabado)"),
        ("Ready for electrical inspection", "Deja listo para la inspección eléctrica")],
       "NEC; rough-in; panels", "electrician_license; osha_10", {"laborer": 0.25}),
    _r("plumber", "lab", "Plumber", "Plomero",
       "plumber; journeyman plumber; apprentice plumber; master plumber; pipefitter; pipe fitter; plumbing helper; "
       "plumbing technician",
       "plomero; plomera; fontanero; fontanera; ayudante de plomero; tubero",
       [("Rough-in and set fixtures to code", "Instala tubería y muebles de baño a código")],
       "rough-in; venting; fixtures", "plumber_license; osha_10", {"laborer": 0.25}),
    _r("hvac technician", "lab services", "HVAC technician", "Técnico de HVAC",
       "hvac technician; hvac tech; hvac installer; hvac; heating and air; ac tech; refrigeration tech; "
       "mechanic hvac; sheet metal worker; ductwork installer",
       "tecnico de hvac; tecnico en aire acondicionado; tecnico de refrigeracion; refrigerista; ductero",
       [("Installs and services heating and cooling", "Instala y da servicio a calefacción y aire")],
       "refrigerant handling; ductwork", "epa_608", {"electrician": 0.25}),
    _r("welder", "lab factory", "Welder", "Soldador",
       "welder; fabricator; welder fabricator; mig welder; tig welder; stick welder; pipe welder; "
       "structural welder; fitter welder",
       "soldador; soldadora; herrero; herrera; pailero",
       [("Welds and fabricates to drawing", "Suelda y fabrica según dibujo")], "MIG; TIG; reading drawings",
       "aws_certified_welder", {}),
    _r("fitter", "factory lab", "Fitter", "Ajustador",
       "fitter; fitter up; assembler fitter; millwright; machine fitter; mechanical fitter",
       "ajustador; ajustadora; armador mecanico; montador; montadora",
       [("Fits and assembles parts to tolerance", "Ajusta y arma piezas a tolerancia")], "measuring; drawings", "",
       {"welder": 0.5, "maker": 0.25}),
    _r("laborer", "lab", "Laborer", "Obrero",
       "laborer; labourer; general laborer; construction laborer; construction worker; helper; construction helper; "
       "hand; site hand; groundman; demo crew; cleanup crew; yard worker",
       "obrero; obrera; peon; peon de obra; ayudante general; ayudante de obra; jornalero; jornalera; chalan",
       [("Site prep, cleanup and carrying for the trades", "Preparación, limpieza y carga para los oficios")],
       "site safety; tools", "osha_10", {}),
    _r("painter", "lab services", "Painter", "Pintor",
       "painter; house painter; commercial painter; finisher painter; paint crew",
       "pintor; pintora; pintor de brocha gorda", [("Preps and paints surfaces", "Prepara y pinta superficies")],
       "surface prep; spraying", "", {"drywall installer": 0.25, "laborer": 0.25}),
    _r("drywall installer", "lab", "Drywall installer", "Instalador de tablaroca",
       "drywall installer; drywaller; drywall hanger; hanger; taper; drywall finisher; mudder; sheetrocker",
       "tablaroquero; tablaroquera; instalador de tablaroca; instalador de drywall; yesero; yesera; durlockero",
       [("Hangs, tapes and finishes drywall", "Instala, encinta y da acabado a tablaroca")], "hanging; taping", "",
       {"painter": 0.25, "carpenter": 0.25}),
    _r("mason", "lab", "Mason", "Albañil",
       "mason; bricklayer; brick mason; block layer; stone mason; stonemason; cement mason; concrete finisher; "
       "concrete worker; tile setter; tiler",
       "albanil; albañil; mampostero; ladrillero; colocador de bloque; concretero; azulejero; colocador de azulejo",
       [("Lays block, brick, stone or concrete to plan", "Coloca block, ladrillo, piedra o concreto según plano")],
       "leveling; mixing", "", {"laborer": 0.25}),
    _r("roofer", "lab", "Roofer", "Techador",
       "roofer; roofing installer; shingler; roof technician", "techador; techadora; techero; techera; impermeabilizador",
       [("Installs and repairs roofing", "Instala y repara techos")], "fall protection", "osha_10", {"laborer": 0.25}),
    _r("equipment operator", "lab", "Equipment operator", "Operador de maquinaria",
       "equipment operator; heavy equipment operator; operator; excavator operator; backhoe operator; "
       "dozer operator; loader operator; crane operator; skid steer operator; bobcat operator",
       "operador de maquinaria; operadora de maquinaria; operador de retroexcavadora; maquinista; operador de grua",
       [("Runs heavy equipment safely on site", "Opera maquinaria pesada con seguridad en la obra")],
       "daily equipment checks; grading", "osha_10", {"forklift operator": 0.25}),
    _r("inspector", "lab", "Inspector", "Inspector",
       "inspector; building inspector; quality inspector; qc inspector; qa inspector; quality control",
       "inspector; inspectora; inspector de calidad; inspectora de calidad; supervisor de calidad",
       [("Checks work against code or spec and records the result", "Revisa el trabajo contra código y anota el resultado")],
       "codes; checklists", "", {"foreman": 0.25}),
    _r("estimator", "lab office", "Estimator", "Estimador",
       "estimator; cost estimator; bid estimator; quantity surveyor; takeoff",
       "estimador; estimadora; presupuestista; cotizador; cotizadora",
       [("Prices jobs from plans", "Cotiza trabajos a partir de planos")], "takeoffs; pricing", "",
       {"project manager": 0.5}),
    # ---------------- factory
    _r("maker", "factory", "Maker", "Operario",
       "maker; production worker; production associate; assembler; line worker; assembly line worker; "
       "machine operator; operator production; manufacturing associate; craftsperson; artisan; sewer; "
       "stitcher; cobbler; packer production",
       "operario; operaria; obrero de produccion; obrera de produccion; ensamblador; ensambladora; "
       "operador de maquina; operadora de maquina; costurero; costurera; zapatero; zapatera; artesano; artesana",
       [("Builds units to the daily quota and logs output", "Produce unidades según la cuota diaria y registra la producción"),
        ("Flags defects at the station", "Reporta defectos en la estación")],
       "station output; quality checks", "", {"fitter": 0.5, "inspector": 0.25}),
    # ---------------- health
    _r("doctor", "health", "Doctor", "Médico",
       "doctor; physician; md; do; provider; attending; dentist; veterinarian; vet",
       "medico; médico; medica; doctor; doctora; dentista; veterinario; veterinaria",
       [("Sees patients by appointment", "Atiende pacientes con cita")], "", "medical_license", {}),
    _r("nurse", "health", "Nurse", "Enfermero",
       "nurse; rn; registered nurse; lpn; lvn; licensed practical nurse; charge nurse; nurse practitioner; np",
       "enfermero; enfermera; enfermera registrada; enfermero registrado; enfermero auxiliar; enfermera auxiliar",
       [("Patient care and charting", "Atención al paciente y expediente")], "charting; vitals", "nursing_license",
       {"nursing assistant": 0.5, "medical assistant": 0.5}),
    _r("nursing assistant", "health", "Nursing assistant", "Auxiliar de enfermería",
       "nursing assistant; cna; certified nursing assistant; patient care tech; pct; caregiver; home health aide; hha",
       "auxiliar de enfermeria; asistente de enfermeria; cuidador; cuidadora; asistente de salud en el hogar",
       [("Helps patients with daily care", "Ayuda a los pacientes con el cuidado diario")], "vitals; patient transfers",
       "cna_cert", {"medical assistant": 0.5}),
    _r("medical assistant", "health", "Medical assistant", "Asistente médico",
       "medical assistant; ma; clinical assistant; patient coordinator; dental assistant",
       "asistente medico; asistente médica; asistente medica; asistente dental",
       [("Rooms patients and takes vitals", "Prepara al paciente y toma signos vitales")], "vitals; scheduling", "",
       {"nursing assistant": 0.5, "receptionist": 0.25}),
    _r("pharmacy technician", "health retail", "Pharmacy technician", "Técnico de farmacia",
       "pharmacy technician; pharmacy tech; cpht; pharmacy assistant; pharmacist; rph",
       "tecnico de farmacia; tecnica de farmacia; auxiliar de farmacia; farmaceutico; farmaceutica; boticario",
       [("Fills and checks prescriptions with the pharmacist", "Prepara y revisa recetas con el farmacéutico")],
       "dosage checks; inventory", "pharmacy_tech_cert", {"cashier": 0.25}),
    # ---------------- services and office
    _r("receptionist", "services health office", "Receptionist", "Recepcionista",
       "receptionist; front desk; front desk associate; front office; scheduler; appointment setter; "
       "patient coordinator front; concierge",
       "recepcionista; recepcion; agendador; agendadora",
       [("Books appointments and handles late fees and no-shows",
         "Agenda citas y maneja cargos por retraso e inasistencias")],
       "scheduling; phones", "", {"host": 0.5, "customer service": 0.5}),
    _r("customer service", "retail services office", "Customer service", "Servicio al cliente",
       "customer service; customer service rep; csr; customer service representative; customer support; "
       "support agent; call center agent; customer care",
       "servicio al cliente; atencion a clientes; representante de servicio; agente de servicio; agente de soporte",
       [("Answers clients and opens DISPUTE tickets", "Atiende clientes y abre tickets de disputa")],
       "de-escalation; order lookup", "", {"receptionist": 0.5, "cashier": 0.25}),
    _r("stylist", "services", "Stylist", "Estilista",
       "stylist; hair stylist; hairstylist; hairdresser; barber; colorist; nail tech; nail technician; "
       "esthetician; aesthetician; makeup artist; massage therapist",
       "estilista; peluquero; peluquera; barbero; barbera; manicurista; cosmetologa; cosmetologo; masajista",
       [("Serves clients by appointment", "Atiende a clientes con cita")], "consultations; booking",
       "cosmetology_license", {}),
    _r("cleaner", "services restaurant retail lab", "Cleaner", "Limpieza",
       "cleaner; janitor; custodian; housekeeper; housekeeping; room attendant; cleaning crew; maid; "
       "sanitation worker; porter building",
       "limpieza; conserje; intendente; afanador; afanadora; camarista; mucama; recamarera; personal de limpieza",
       [("Cleans to the checklist and logs it", "Limpia según la lista y lo registra")], "chemical safety", "",
       {"dishwasher": 0.25}),
    _r("bookkeeper", "office", "Bookkeeper", "Contador",
       "bookkeeper; accountant; accounting clerk; ap clerk; ar clerk; accounts payable; accounts receivable; "
       "payroll clerk; controller; finance assistant",
       "contador; contadora; auxiliar contable; tenedor de libros; capturista contable",
       [("Matches invoices, receipts and the books", "Cuadra facturas, recibos y los libros")],
       "QuickBooks; reconciliation", "", {}),
    _r("office assistant", "office", "Office assistant", "Asistente de oficina",
       "office assistant; administrative assistant; admin assistant; admin; office manager; secretary; "
       "executive assistant; hr assistant; human resources; data entry; clerical",
       "asistente de oficina; asistente administrativo; asistente administrativa; secretario; secretaria; "
       "auxiliar administrativo; auxiliar administrativa; recursos humanos; capturista",
       [("Keeps files, calls and the calendar", "Lleva archivos, llamadas y la agenda")], "office software", "",
       {"receptionist": 0.5}),
]

BY_KEY = {r.key: r for r in ROLES}


# ---------------------------------------------------------------- matching
# Short forms people type, spelled out before matching.
SHORT = {"sr": "senior", "snr": "senior", "jr": "junior", "asst": "assistant", "assoc": "associate",
         "mgr": "manager", "mngr": "manager", "supv": "supervisor", "supvr": "supervisor", "sup": "supervisor",
         "gral": "general", "aux": "auxiliar"}

# One-word titles too vague to pick a role from inside a longer title ("cook helper" is a cook, not a
# laborer). They still match when they are the whole title.
GENERIC = {"helper", "hand", "associate", "clerk", "operator", "driver", "register", "counter", "till", "window",
           "pass", "utility", "security", "super", "do", "ma", "am", "pm", "md", "np", "ap", "lp", "km", "gm",
           "mod", "admin", "hanger", "freight", "inbound", "visual", "delivery", "warehouse", "cabo", "caja",
           "recepcion", "seguridad", "limpieza", "supervisor", "encargado", "encargada", "manager", "gerente",
           "provider", "fry", "pantry", "dish", "porter", "steward", "plancha", "captain", "vet"}


def norm(text):
    """Lowercase, no accents, punctuation as spaces, short forms spelled out.
    'Cocinero de Línea II' -> 'cocinero de linea ii', 'Asst. Mgr' -> 'assistant manager'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("&", " and ").replace("'", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(SHORT.get(w, w) for w in t.split())


def _index():
    idx = {}
    for r in ROLES:
        for a, lang in [(r.key, "en"), (r.en, "en"), (r.es, "es")] + [(a, "en") for a in r.aliases_en] + \
                       [(a, "es") for a in r.aliases_es]:
            idx.setdefault(norm(a), (r.key, lang))
    return idx


INDEX = _index()


def _house_titles(db):
    """Current house titles: {normalized title: role}. Latest event per title wins; retired ones drop out."""
    if db is None:
        return {}
    try:
        rows = db.execute("SELECT title_norm, role, action FROM role_title_events ORDER BY id").fetchall()
    except Exception:            # table not there yet (older database)
        return {}
    out = {}
    for t, role, action in rows:
        if action == "retire":
            out.pop(t, None)
        else:
            out[t] = role
    return out


def match(title, db=None):
    """What schedule role a job title belongs to.

    Returns {"role", "label_en", "label_es", "matched", "lang", "seniority", "schedule", "also", "extra", "how"}
    where also lists a second role in the same title ("server/bartender") and how is
    "house" (this business's own word), "exact", "contains" (a known title inside a longer one, like
    "lead line cook nights"), or None with "suggestions" when nothing is close enough to be sure.
    """
    n = norm(title)
    out = {"title": title, "role": None, "matched": None, "lang": None, "how": None, "seniority": [], "schedule": [],
           "also": [], "extra": [], "suggestions": []}
    if not n:
        return out
    house = _house_titles(db)
    if n in house:
        return _fill(out, house[n], n, None, "house", [])
    if n in INDEX:
        key, lang = INDEX[n]
        return _fill(out, key, n, lang, "exact", [])
    words = n.split()
    # longest known title inside the text, by words then letters; house titles first
    best = None
    for size in range(len(words), 0, -1):
        for i in range(len(words) - size + 1):
            piece = " ".join(words[i:i + size])
            hit = ("house", house[piece], None) if piece in house else \
                  ("contains", *INDEX[piece]) if piece in INDEX and piece not in GENERIC else None
            if hit and (best is None or (size, len(piece)) > (best[0], len(best[2]))):
                best = (size, i, piece, hit)
        if best:
            break
    if best:
        size, i, piece, (how, key, lang) = best
        rest = words[:i] + words[i + size:]
        return _fill(out, key, piece, lang, how, rest)
    # only vague words: take one if it's the only role in sight ("night supervisor" -> shift lead)
    vague = {INDEX[w][0] for w in words if w in INDEX}
    if len(vague) == 1:
        w = next(w for w in words if w in INDEX)
        return _fill(out, INDEX[w][0], w, INDEX[w][1], "contains", [x for x in words if x != w])
    out["suggestions"] = suggest(title, db)
    return out


def _fill(out, key, matched, lang, how, rest):
    r = BY_KEY.get(key)
    out.update(role=key, matched=matched, lang=lang, how=how,
               label_en=r.en if r else key, label_es=r.es if r else key)
    for w in rest:
        if w in SENIORITY:
            if SENIORITY[w] not in out["seniority"]:
                out["seniority"].append(SENIORITY[w])
        elif w in SCHEDULE_WORDS:
            if SCHEDULE_WORDS[w] and SCHEDULE_WORDS[w] not in out["schedule"]:
                out["schedule"].append(SCHEDULE_WORDS[w])
        elif w in INDEX and INDEX[w][0] != key:
            if INDEX[w][0] not in out["also"]:
                out["also"].append(INDEX[w][0])   # a second role in the title, like "server/bartender"
        else:
            out["extra"].append(w)     # a station or specialty, like "produce" in "produce manager"
    return out


def suggest(title, db=None, n=3):
    """Close spellings, for a person to pick from. Never applied on its own."""
    names = list(INDEX) + list(_house_titles(db))
    roles = []
    for hit in difflib.get_close_matches(norm(title), names, n=8, cutoff=0.75):
        key = _house_titles(db).get(hit) or INDEX[hit][0]
        if key not in roles:
            roles.append(key)
    return roles[:n]


def role_for(title, db=None):
    """The schedule role key for a title, or None if not sure. Only exact, house and contained titles count."""
    return match(title, db)["role"]


def label(key, lang="en"):
    r = BY_KEY.get(key)
    if not r:
        return key
    return r.es if lang == "es" else r.en


def describe(key, lang="en"):
    """What the job involves, for Set up role and job posts. Duties in the reader's language."""
    r = BY_KEY.get(key)
    if not r:
        return None
    i = 1 if lang == "es" else 0
    return {"role": r.key, "label": r.es if lang == "es" else r.en, "trades": list(r.trades),
            "duties": [d[i] for d in r.duties], "skills": list(r.skills), "usual_certs": list(r.certs),
            "also_called": list(r.aliases_es if lang == "es" else r.aliases_en)[:8],
            "experience_counts_from": {k: v for k, v in sorted(r.transfers.items(), key=lambda kv: -kv[1])}}


def roles_for_trade(trade):
    return [r.key for r in ROLES if trade in r.trades]


# ---------------------------------------------------------------- experience across titles
def experience(db, candidate_id, role):
    """Months that count toward `role`, from every title on the candidate's history.

    Months under any title for the same role count in full. Months in a related role count at its share
    (ROLES[...].transfers), rounded down. Returns (months, lines) where lines explain each part.
    """
    target = BY_KEY.get(role)
    full, partial, lines = 0, 0.0, []
    for row in db.execute("SELECT role, months FROM candidate_experience WHERE candidate_id=?", (candidate_id,)):
        typed, months = row[0], row[1]
        key = role_for(typed, db) or norm(typed)
        if key == role or norm(typed) == norm(role):
            full += months
            if norm(typed) != norm(role):
                lines.append(f"{months} months as {typed} (counts as {role})")
        elif target and key in target.transfers:
            share = target.transfers[key]
            partial += months * share
            lines.append(f"{months} months as {typed} (related: {int(share * 100)}% counts)")
    return full + int(partial), lines


# ---------------------------------------------------------------- house titles
def add_house_title(db, title, role, actor):
    """A word only this business uses, like 'pit boss' for the grill cook. Manager and up. Append-only."""
    from hiwr_access import level, LEVEL_NAMES
    if level(db, actor) < HOUSE_TITLE_LEVEL:
        raise PermissionError(f"House titles need level {HOUSE_TITLE_LEVEL} ({LEVEL_NAMES[HOUSE_TITLE_LEVEL]})")
    n = norm(title)
    if not n or len(n) > 40:
        raise ValueError("A house title is 1 to 40 characters")
    role = " ".join((role or "").split()).lower()
    if not role:
        raise ValueError("Pick the schedule role this title belongs to")
    if n in INDEX and INDEX[n][0] != role:
        raise ValueError(f"'{title}' already means {INDEX[n][0]}; pick another word")
    db.execute("INSERT INTO role_title_events(title_norm, title, role, action, actor) VALUES (?,?,?,?,?)",
               (n, title.strip(), role, "add", actor))
    return n


def retire_house_title(db, title, actor):
    from hiwr_access import level, LEVEL_NAMES
    if level(db, actor) < HOUSE_TITLE_LEVEL:
        raise PermissionError(f"House titles need level {HOUSE_TITLE_LEVEL} ({LEVEL_NAMES[HOUSE_TITLE_LEVEL]})")
    n = norm(title)
    role = _house_titles(db).get(n)
    if role is None:
        raise LookupError(f"'{title}' is not a house title")
    db.execute("INSERT INTO role_title_events(title_norm, title, role, action, actor) VALUES (?,?,?,?,?)",
               (n, title.strip(), role, "retire", actor))
