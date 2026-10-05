"""Sugestões conservadoras, sem chamadas pagas nem envio adicional de dados."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from collections import defaultdict

def normalize(value):
    value = "".join(c for c in unicodedata.normalize("NFKD", str(value).lower())
                    if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()

# Termos específicos evitam classificar água mineral como conta de água,
# energia de um alimento como luz e nomes genéricos de lojas como supermercado.
RULES = {
    "Água": (r"conta de agua|fatura de agua|agua e esgoto|saneamento|sabesp|sanepar|copasa|casan|cedae",),
    "Energia": (r"conta de luz|conta de energia|energia eletrica|fatura de energia|celesc|cemig|copel|enel|energisa|equatorial",),
    "Supermercado": (r"supermercado|supermercados|hipermercado|atacadao|assai|atacarejo|mercearia|hortifruti|acougue|mercado|feira",
                      r"arroz|feijao|leite|macarrao|farinha|acucar|cafe em po|oleo de soja|ovos|banana|tomate|agua mineral|detergente|sabao|papel higienico"),
    "Combustível": (r"gasolina|etanol|diesel|abastecimento|abasteci|posto de combustivel|posto de gasolina|gnv",),
    "Moradia": (r"aluguel|condominio|financiamento imobiliario|prestacao da casa|prestacao do apartamento",),
    "Internet e telefone": (r"internet|banda larga|fibra optica|conta de telefone|plano de celular|recarga de celular|telefonia|telecom",),
    "Saúde": (r"farmacia|drogaria|medicamento|remedio|consulta medica|dentista|odontologia|hospital|plano de saude|exame medico|clinica|fisioterapia",),
    "Educação": (r"escola|escolar|mensalidade escolar|faculdade|universidade|curso|material escolar|livro didatico|creche|apostila",),
    "Transporte": (r"uber|99pop|taxi|onibus|metro|passagem|pedagio|estacionamento|oficina mecanica|mecanico|pneu|pneus|troca de oleo|seguro do carro",),
    "Casa e manutenção": (r"mesa|cadeira|cadeiras|sofa|armario|cama|colchao|geladeira|fogao|lavadora|micro ondas|moveis|mobilia|abracadeira|abracadeiras|material de construcao|ferragem|ferragens|tinta|cimento|torneira|encanador|eletricista|botijao|gas de cozinha|conserto|reforma",),
    "Impostos e taxas": (r"iptu|ipva|licenciamento|imposto|impostos|taxa de licenciamento|darf|das mei",),
    "Lazer": (r"cinema|teatro|show|ingresso|passeio|parque de diversoes|videogame|brinquedo|brinquedos|streaming|netflix|spotify|viagem|hotel|pousada|lazer",),
    "Vestuário": (r"roupa|roupas|camiseta|camisa|calca|vestido|sapato|sapatos|tenis|calcado|calcados|bermuda|blusa|jaqueta|meias",),
    "Pets": (r"pet shop|petshop|veterinario|veterinaria|racao|areia para gatos|banho e tosa",),
    "Alimentação fora de casa": (r"cachorro quente|hot dog|hamburguer|hamburger|lanche|lanchonete|restaurante|restaurantes|pizzaria|pizza|delivery|ifood|pastel|sorvete|sorveteria|padaria|cafe da manha fora",),
}

def matches(text, expression):
    return bool(re.search(r"\b(?:" + expression + r")\b", text))

def classify_text(value, merchant=False):
    text = normalize(value)
    if not text:
        return "Outros"
    # Loja conhecida por tipo de atividade prevalece sobre palavras nos produtos.
    if merchant:
        if matches(text, RULES["Supermercado"][0]):
            return "Supermercado"
        if matches(text, r"farmacia|drogaria"):
            return "Saúde"
        if matches(text, r"pet shop|petshop|veterinario|veterinaria"):
            return "Pets"
        if matches(text, r"restaurante|lanchonete|pizzaria|padaria|sorveteria"):
            return "Alimentação fora de casa"
    short_labels = {"agua": "Água", "luz": "Energia", "forca": "Energia",
                    "energia": "Energia", "telefone": "Internet e telefone"}
    if text in short_labels:
        return short_labels[text]
    hits = [cat for cat, expressions in RULES.items()
            if any(matches(text, expression) for expression in expressions)]
    if len(hits) > 1 and "Lazer" in hits and matches(text, "lazer"):
        if not matches(text, RULES["Lazer"][0].replace("|lazer", "")):
            hits.remove("Lazer")
    # Água mineral e conta de água podem ser citadas juntas: não adivinhar.
    return hits[0] if len(hits) == 1 else "Outros"

def suggest_category(description, items=None, amount=None):
    category = classify_text(description, merchant=True)
    if category != "Outros":
        return category
    totals = defaultdict(Decimal)
    count = defaultdict(int)
    for item in items or []:
        category = classify_text(item.get("descricao", item.get("description", "")))
        if category == "Outros":
            continue
        count[category] += 1
        try:
            subtotal = Decimal(str(item.get("subtotal", "0")).replace(",", "."))
            if subtotal.is_finite() and subtotal > 0:
                totals[category] += subtotal
        except (InvalidOperation, ValueError):
            pass
    try:
        total = Decimal(str(amount))
        if not total.is_finite():
            return "Outros"
    except (InvalidOperation, ValueError):
        total = Decimal("0")
    if totals and total > 0:
        category, value = max(totals.items(), key=lambda entry: entry[1])
        # Só sugere a categoria da compra se ela explicar a maior parte do total.
        return category if value >= total * Decimal("0.65") and value <= total * Decimal("1.05") else "Outros"
    if len(count) == 1 and not amount:
        return next(iter(count))
    return "Outros"
