"""Seed catalog with demo products."""

from app.db import clear_products, init_db, upsert_product
from app.schemas import Product, VolumeTier

PRODUCTS: list[Product] = [
    Product(
        id="elec-conn-001",
        name="Industrial Connector Component",
        category="electronics",
        base_unit_price=12.0,
        stock=20000,
        lead_time_min_days=7,
        lead_time_max_days=21,
        volume_tiers=[
            VolumeTier(min_qty=1000, unit_price=12.0, floor_price=11.20),
            VolumeTier(min_qty=2000, unit_price=11.40, floor_price=10.80),
            VolumeTier(min_qty=5000, unit_price=10.90, floor_price=10.30),
            VolumeTier(min_qty=10000, unit_price=10.50, floor_price=9.90),
        ],
    ),
    Product(
        id="elec-pcb-002",
        name="4-Layer PCB Assembly Board",
        category="electronics",
        base_unit_price=45.0,
        stock=5000,
        lead_time_min_days=14,
        lead_time_max_days=28,
        volume_tiers=[
            VolumeTier(min_qty=100, unit_price=45.0, floor_price=42.50),
            VolumeTier(min_qty=500, unit_price=42.0, floor_price=39.80),
            VolumeTier(min_qty=1000, unit_price=39.5, floor_price=37.20),
        ],
    ),
    Product(
        id="elec-mcu-003",
        name="ARM Microcontroller Module",
        category="electronics",
        base_unit_price=28.0,
        stock=15000,
        lead_time_min_days=10,
        lead_time_max_days=21,
        volume_tiers=[
            VolumeTier(min_qty=500, unit_price=28.0, floor_price=26.40),
            VolumeTier(min_qty=2000, unit_price=26.5, floor_price=25.00),
            VolumeTier(min_qty=5000, unit_price=25.0, floor_price=23.60),
            VolumeTier(min_qty=10000, unit_price=23.5, floor_price=22.10),
        ],
    ),
    Product(
        id="elec-psu-004",
        name="24V Industrial Power Supply Unit",
        category="electronics",
        base_unit_price=85.0,
        stock=3000,
        lead_time_min_days=14,
        lead_time_max_days=35,
        volume_tiers=[
            VolumeTier(min_qty=50, unit_price=85.0, floor_price=80.00),
            VolumeTier(min_qty=200, unit_price=80.0, floor_price=75.50),
            VolumeTier(min_qty=500, unit_price=76.0, floor_price=71.80),
        ],
    ),
    Product(
        id="ind-brkt-001",
        name="Galvanized Steel Mounting Bracket",
        category="industrial",
        base_unit_price=8.5,
        stock=50000,
        lead_time_min_days=5,
        lead_time_max_days=14,
        volume_tiers=[
            VolumeTier(min_qty=2000, unit_price=8.5, floor_price=7.90),
            VolumeTier(min_qty=5000, unit_price=8.0, floor_price=7.45),
            VolumeTier(min_qty=10000, unit_price=7.5, floor_price=7.00),
            VolumeTier(min_qty=25000, unit_price=7.0, floor_price=6.55),
        ],
    ),
    Product(
        id="ind-valv-002",
        name="Hydraulic Control Valve",
        category="industrial",
        base_unit_price=120.0,
        stock=2500,
        lead_time_min_days=21,
        lead_time_max_days=45,
        volume_tiers=[
            VolumeTier(min_qty=25, unit_price=120.0, floor_price=112.00),
            VolumeTier(min_qty=100, unit_price=115.0, floor_price=107.50),
            VolumeTier(min_qty=250, unit_price=110.0, floor_price=103.00),
        ],
    ),
    Product(
        id="ind-cnv-003",
        name="Modular Conveyor Belt Segment",
        category="industrial",
        base_unit_price=350.0,
        stock=800,
        lead_time_min_days=28,
        lead_time_max_days=56,
        volume_tiers=[
            VolumeTier(min_qty=10, unit_price=350.0, floor_price=328.00),
            VolumeTier(min_qty=50, unit_price=335.0, floor_price=314.00),
            VolumeTier(min_qty=100, unit_price=320.0, floor_price=300.00),
        ],
    ),
    Product(
        id="pkg-box-001",
        name="Corrugated Shipping Box (Large)",
        category="packaging",
        base_unit_price=3.2,
        stock=100000,
        lead_time_min_days=3,
        lead_time_max_days=7,
        volume_tiers=[
            VolumeTier(min_qty=5000, unit_price=3.2, floor_price=2.95),
            VolumeTier(min_qty=20000, unit_price=2.9, floor_price=2.70),
            VolumeTier(min_qty=50000, unit_price=2.7, floor_price=2.50),
            VolumeTier(min_qty=100000, unit_price=2.5, floor_price=2.32),
        ],
    ),
    Product(
        id="pkg-shrk-002",
        name="Industrial Shrink Wrap Roll",
        category="packaging",
        base_unit_price=18.0,
        stock=12000,
        lead_time_min_days=5,
        lead_time_max_days=10,
        volume_tiers=[
            VolumeTier(min_qty=500, unit_price=18.0, floor_price=16.80),
            VolumeTier(min_qty=2000, unit_price=16.5, floor_price=15.40),
            VolumeTier(min_qty=5000, unit_price=15.5, floor_price=14.45),
        ],
    ),
    Product(
        id="pkg-pllt-003",
        name="Stretch Pallet Wrap Film",
        category="packaging",
        base_unit_price=12.0,
        stock=25000,
        lead_time_min_days=3,
        lead_time_max_days=10,
        volume_tiers=[
            VolumeTier(min_qty=1000, unit_price=12.0, floor_price=11.15),
            VolumeTier(min_qty=5000, unit_price=11.0, floor_price=10.25),
            VolumeTier(min_qty=10000, unit_price=10.5, floor_price=9.75),
            VolumeTier(min_qty=20000, unit_price=10.0, floor_price=9.30),
        ],
    ),
]


def seed_products() -> int:
    clear_products()
    for product in PRODUCTS:
        upsert_product(product)
    return len(PRODUCTS)


if __name__ == "__main__":
    init_db()
    count = seed_products()
    print(f"Seeded {count} products across electronics, industrial, and packaging.")
