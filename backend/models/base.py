from pydantic import BaseModel as PydanticBaseModel
from typing import Optional

class StorePrice(PydanticBaseModel):
    """
    Model used to store price information for a product in a specific store.

    Members:
        store (str): The name of the store
        price (Optional[float]): The current price of the product in the store
        regular_price (Optional[float]): The regular price of the product in the store
        on_sale (bool): Whether the product is currently on sale in the store
        sale_conditions (Optional[str]): Any conditions that apply to the sale (e.g., "Buy one get one free")
        in_stock (bool): Whether the product is currently in stock in the store
    """
    store: str
    price: Optional[float] = None
    regular_price: Optional[float] = None
    on_sale: bool = False
    sale_conditions: Optional[str] = None
    in_stock: bool = True

class Product(PydanticBaseModel):
    """
    Model used to store information about a product.

    Members:
        id (str): The unique identifier for the product
        name (str): The name of the product
        brand (Optional[str]): The brand of the product
        size (Optional[str]): The size or quantity of the product (e.g., "500ml", "1kg")
        price (Optional[float]): The current price of the product
        regular_price (Optional[float]): The regular price of the product
        on_sale (bool): Whether the product is currently on sale
        sale_conditions (Optional[str]): Any conditions that apply to the sale (e.g., "Buy one get one free")
        image_url (Optional[str]): A URL to an image of the product
        store (str): The name of the store where the product is available
        in_stock (bool): Whether the product is currently in stock
        upc (Optional[str]): The Universal Product Code for the product
        descriptors (list[str]): A list of descriptors or tags associated with the product
        store_locations (list[str]): A list of the item's locations in different stores (e.g., "Aisle 3", "Frozen Foods")
        store_prices (list[StorePrice]): A list of StorePrice objects representing prices in different stores
    """
    id: str
    name: str
    brand: Optional[str] = None
    size: Optional[str] = None
    price: Optional[float] = None
    regular_price: Optional[float] = None
    on_sale: bool = False
    store_location: Optional[str] = None
    sale_conditions: Optional[str] = None
    image_url: Optional[str] = None
    store: str
    in_stock: bool = True
    upc: Optional[str] = None
    descriptors: list[str] = []
    store_locations: list[str] = []
    store_prices: list[StorePrice] = []